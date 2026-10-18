"""The gated LangGraph loop: START -> db_gate -> agent -> post_gate -> END.

- `db_gate` is deterministic and PRE-LLM: liveness (DB_OFFLINE) and
  population/rank completeness (GOVERNANCE_EMPTY_GRAPH). Either abort routes
  to post_gate without entering the agent node — zero model tokens.
- `agent` node builds `create_agent(...)` LAZILY on first entry, so abort
  paths never even construct a provider client (kill-switch works keyless).
- `post_gate` is the anti-bypass: a final message containing code tokens
  with no successful graph-tool call this turn is overwritten with
  policy.BYPASS_REFUSAL, deterministically.

Deviation (ledger): plan types `build_graph` sync; it is async because the
fastmcp in-memory Client is async-only — the MCP session is entered for the
graph's lifetime and released by `close_graph`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Annotated, Any, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from codegraph_sentinel import config, policy
from codegraph_sentinel.graph_gateway import GraphUnavailableError
from codegraph_sentinel.mcp_tools_adapter import load_mcp_tools

# Line-anchored (not bare substring): "undefined " must not trip the bypass —
# ledger ruling: fence + def/class at line start matches real code payloads.
_CODE_TOKEN = re.compile(r"(^|\n)\s*(def |class )")


class SentinelState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    aborted: bool
    abort_reason: str
    # Ruling: post_gate needs per-turn bookkeeping; db_gate resets it every
    # turn, agent_node ORs in this turn's successful tool evidence.
    tool_evidence: bool


def _has_code(text: str) -> bool:
    return "```" in text or bool(_CODE_TOKEN.search(text))


def _successful_tool(message: BaseMessage) -> bool:
    """A graph-tool call whose payload was a real result (not DB_UNAVAILABLE,
    not an exception). unknown_function counts: the tool executed fine."""
    if not isinstance(message, ToolMessage):
        return False
    content = message.content
    if not isinstance(content, str):
        return False
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("status") != "DB_UNAVAILABLE"


# --- checkpointer (persists conversation across turns) ---------------------
# Keyed by (path, running loop): each event loop gets its own aiosqlite
# connection — reusing one across loops raises "no active connection"
# (pytest-asyncio runs every test in a fresh loop; single-loop apps such as
# the CLI build exactly one handle and keep it for the process lifetime).
_SAVERS: dict[tuple[str, Any], Any] = {}


async def _checkpointer(path: str):
    loop = asyncio.get_running_loop()
    key = (path, loop)
    if key not in _SAVERS:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        cm = AsyncSqliteSaver.from_conn_string(path)
        saver = await cm.__aenter__()  # held for this loop's lifetime
        _SAVERS[key] = (cm, saver)
        # drop handles bound to closed loops (their threads are daemons)
        for stale in [
            k for k in list(_SAVERS) if k[1] is not loop and k[1].is_closed()
        ]:
            _SAVERS.pop(stale, None)
    return _SAVERS[key][1]


# --- MCP sessions (one per built graph) ------------------------------------
_SESSIONS: dict[int, Any] = {}


async def build_graph(gateway, mcp, model=None):
    """Compile the gated loop. `gateway` feeds ONLY the pre-flight gate; the
    MCP tools read whatever gateway was wired via mcp_server.set_gateway —
    that split is what makes the layer-2 (mid-conversation) kill testable."""
    tools, session_cm = await load_mcp_tools(mcp)
    await session_cm.__aenter__()
    agent_box: dict[str, Any] = {}

    async def agent_node(state: SentinelState) -> dict:
        if "agent" not in agent_box:
            agent_box["agent"] = create_agent(
                model=model if model is not None else config.build_model(),
                tools=tools,
                system_prompt=policy.DECISION_POLICY,
            )
        previous_ids = {m.id for m in state["messages"]}
        result = await agent_box["agent"].ainvoke({"messages": state["messages"]})
        evidence = any(
            _successful_tool(m) for m in result["messages"] if m.id not in previous_ids
        )
        return {"messages": result["messages"], "tool_evidence": evidence}

    def db_gate(state: SentinelState) -> dict:
        updates: dict[str, Any] = {"tool_evidence": False}
        try:
            gateway.ping()
            total = int(
                gateway.query(
                    "MATCH (n:Function) RETURN count(n)", read_only=True
                ).result_set[0][0]
            )
            unranked = int(
                gateway.query(
                    "MATCH (n:Function) WHERE n.pagerank IS NULL RETURN count(n)",
                    read_only=True,
                ).result_set[0][0]
            )
        except GraphUnavailableError:
            updates.update(
                aborted=True,
                abort_reason="DB_OFFLINE",
                messages=[AIMessage(policy.DB_OFFLINE_REFUSAL)],
            )
            return updates
        if total == 0 or unranked > 0:
            updates.update(
                aborted=True,
                abort_reason="GOVERNANCE_EMPTY_GRAPH",
                messages=[AIMessage(policy.EMPTY_GRAPH_REFUSAL)],
            )
            return updates
        updates.update(aborted=False, abort_reason="")
        return updates

    def post_gate(state: SentinelState) -> dict:
        if state.get("aborted"):
            return {}  # gate refusal is already the final message
        messages = list(state["messages"])
        if not messages:
            return {}
        last = messages[-1]
        if (
            isinstance(last, AIMessage)
            and _has_code(str(last.content))
            and not state.get("tool_evidence")
        ):
            messages[-1] = AIMessage(policy.BYPASS_REFUSAL, id=last.id)
            return {"messages": messages}
        return {}

    def route(state: SentinelState) -> str:
        return "post_gate" if state.get("aborted") else "agent"

    workflow = StateGraph(SentinelState)
    workflow.add_node("db_gate", db_gate)
    workflow.add_node("agent", agent_node)
    workflow.add_node("post_gate", post_gate)
    workflow.add_edge(START, "db_gate")
    workflow.add_conditional_edges(
        "db_gate", route, {"agent": "agent", "post_gate": "post_gate"}
    )
    workflow.add_edge("agent", "post_gate")
    workflow.add_edge("post_gate", END)

    graph = workflow.compile(checkpointer=await _checkpointer(config.CHECKPOINT_PATH))
    _SESSIONS[id(graph)] = (session_cm, tools)
    return graph


async def close_graph(graph) -> None:
    """Release the graph's MCP session (the checkpointer is process-wide)."""
    handle = _SESSIONS.pop(id(graph), None)
    if handle is not None:
        session_cm, _ = handle
        await session_cm.__aexit__(None, None, None)


async def run_turn(
    gateway, mcp, user_msg: str, thread_id: str = "cli", model=None
) -> dict:
    """One governed turn: build, invoke, release. Returns the final state."""
    graph = await build_graph(gateway, mcp, model=model)
    try:
        return await graph.ainvoke(
            {"messages": [HumanMessage(user_msg)]},
            config={"configurable": {"thread_id": thread_id}},
        )
    finally:
        await close_graph(graph)