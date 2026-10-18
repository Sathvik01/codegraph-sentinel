"""C5 gate/policy tests: deterministic pre-LLM aborts and the anti-bypass override.

All models here are scripted fakes — the ZERO-token pins assert the gate
routes around the agent node entirely, so `call_count == 0` is mechanical.
"""

import uuid

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from codegraph_sentinel import policy
from codegraph_sentinel.agent_graph import run_turn
from codegraph_sentinel.graph_gateway import GraphGateway, GraphUnavailableError

FIVE = {
    "list_functions",
    "get_centrality",
    "get_blast_radius",
    "get_memory_rules",
    "record_architectural_rule",
}


class FakeToolModel(GenericFakeChatModel):
    """Scripted model with a call counter.

    bind_tools must be overridden: BaseChatModel's default raises
    NotImplementedError, and create_agent always binds the five tools.
    """

    counter: dict = Field(default_factory=lambda: {"calls": 0})

    @property
    def call_count(self) -> int:
        return self.counter["calls"]

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002 - scripted, ignores tools
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.counter["calls"] += 1
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def fake_model(*responses) -> FakeToolModel:
    return FakeToolModel(messages=iter(responses))


def tid(prefix: str) -> str:
    """Fresh thread id per run — checkpoint file persists across sessions."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def tool_call_msg(name: str, **args) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": "call-1", "type": "tool_call"}],
    )


async def test_offline_db_blocks_before_llm(gw_ingested, mcp_app, monkeypatch):
    model = fake_model(AIMessage("this must never be generated"))

    def dead_ping():
        raise GraphUnavailableError("simulated outage")

    monkeypatch.setattr(gw_ingested, "ping", dead_ping)
    state = await run_turn(
        gw_ingested, mcp_app, "Refactor format_timestamp", thread_id=tid("t-offline"), model=model
    )
    assert state["aborted"] is True and state["abort_reason"] == "DB_OFFLINE"
    assert model.call_count == 0  # ZERO model tokens spent
    final = state["messages"][-1].content
    assert "def " not in final and "```" not in final  # no code token


async def test_alive_db_proceeds_to_agent(gw_ingested, mcp_app):
    model = fake_model(AIMessage("The architecture graph tracks seven functions."))
    state = await run_turn(
        gw_ingested, mcp_app, "What functions exist?", thread_id=tid("t-alive"), model=model
    )
    assert state["aborted"] is False
    assert model.call_count == 1
    assert model.call_count >= 1 and state["messages"][-1].content.startswith("The architecture")


async def test_db_unavailable_payload_forces_refusal(gw_ingested, mcp_app):
    # Gate sees a live server; the wired tools gateway dies mid-conversation
    # (the layer-2 kill: payload policy, not the gate).
    gw_ingested.close()
    live = GraphGateway.open()
    try:
        model = fake_model(
            tool_call_msg("get_blast_radius", target_func="parse_config"),
            AIMessage(
                "The governance layer is offline: DB_UNAVAILABLE. No code is written."
            ),
        )
        state = await run_turn(
            live, mcp_app, "Refactor parse_config", thread_id=tid("t-dbdead"), model=model
        )
        assert state["aborted"] is False  # gate passed — tools caught the death
        tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
        assert tool_msgs and "DB_UNAVAILABLE" in str(tool_msgs[-1].content)
        final = state["messages"][-1].content
        assert "DB_UNAVAILABLE" in final and "validate" not in final
    finally:
        live.close()


async def test_empty_recreated_file_blocks_without_llm(gw_ingested, mcp_app):
    # Server stays alive (ping passes); governance content is gone —
    # the plan's "deleted .rdb recreated empty" case, adapted to a live server.
    gw_ingested.graph().query("MATCH (n) DETACH DELETE n")
    model = fake_model(AIMessage("this must never be generated"))
    state = await run_turn(
        gw_ingested, mcp_app, "Refactor validate_identity", thread_id=tid("t-empty"), model=model
    )
    assert state["aborted"] is True and state["abort_reason"] == "GOVERNANCE_EMPTY_GRAPH"
    assert model.call_count == 0


async def test_unranked_function_blocks_without_llm(gw_ingested, mcp_app):
    # Population check part two: any Function missing pagerank aborts too.
    gw_ingested.graph().query("CREATE (:Function {name: 'half_ranked'})")
    model = fake_model(AIMessage("this must never be generated"))
    state = await run_turn(
        gw_ingested, mcp_app, "Refactor validate_identity", thread_id=tid("t-unranked"), model=model
    )
    assert state["aborted"] is True and state["abort_reason"] == "GOVERNANCE_EMPTY_GRAPH"
    assert model.call_count == 0


async def test_pasted_code_without_tools_is_overridden(gw_ingested, mcp_app):
    model = fake_model(
        AIMessage(
            "Sure! Here is the refactor:\n```python\ndef parse_config(raw):\n    return {}\n```"
        )
    )
    state = await run_turn(
        gw_ingested,
        mcp_app,
        "Refactor this snippet: def parse_config(): ...",
        thread_id=tid("t-paste"),
        model=model,
    )
    final = state["messages"][-1].content
    assert policy.BYPASS_REFUSAL in final
    assert "def " not in final and "```" not in final


async def test_unknown_function_blocks_improvisation(gw_ingested, mcp_app):
    model = fake_model(
        tool_call_msg("get_centrality", target_func="mystery_helper"),
        AIMessage(policy.UNKNOWN_FUNCTION_REFUSAL),
    )
    state = await run_turn(
        gw_ingested, mcp_app, "Refactor mystery_helper", thread_id=tid("t-unknown"), model=model
    )
    assert policy.UNKNOWN_FUNCTION_REFUSAL in state["messages"][-1].content


async def test_policy_has_no_query_vocabulary():
    """The system prompt must contain zero Cypher/node-label vocabulary."""
    import re

    banned = ["MATCH", "RETURN", "CREATE (", "MERGE", ":Function", ":CALLS", "pagerank IS"]
    for word in banned:
        assert word not in policy.DECISION_POLICY, f"policy leaks query vocabulary: {word}"
    assert not re.search(r"\bCypher\b", policy.DECISION_POLICY, re.IGNORECASE), (
        "policy must never mention Cypher"
    )
    # threshold wired from config (calibrated), not a hardcoded 0.85
    from codegraph_sentinel import config

    assert str(config.PAGERANK_BLOCK_THRESHOLD) in policy.DECISION_POLICY
    assert "0.85" not in policy.DECISION_POLICY