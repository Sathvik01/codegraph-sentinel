"""C6 cross-session persistence: thread_id checkpoints + graph-native memory.

Two independent persistence layers, both pinned:
1. Conversation history lives in the SQLite checkpointer keyed by
   `thread_id` — restored after a simulated process teardown (gateway
   closed, saver handle dropped, fresh build), not hallucinated cold-start.
2. Architectural memory rules live in the GRAPH itself — they survive
   gateway close/reopen AND full repo re-ingestion with no checkpointer
   involved at all (visible even in a thread that never existed).

All models are scripted fakes — no live LLM calls.
"""

import uuid

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from codegraph_sentinel import config, graph_tools
from codegraph_sentinel.agent_graph import run_turn
from codegraph_sentinel.graph_gateway import GraphGateway
from codegraph_sentinel.ingest import ingest
from codegraph_sentinel.mcp_server import set_gateway


class FakeToolModel(GenericFakeChatModel):
    """Scripted model with a call counter (see test_agent_graph: BaseChatModel's
    default bind_tools raises NotImplementedError and create_agent always binds)."""

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
    """Fresh thread id per run — the checkpoint file persists across runs."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def tool_call_msg(name: str, **args) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": "call-1", "type": "tool_call"}],
    )


async def _drop_checkpoint_handle() -> None:
    """Evict this loop's saver handle so the next build REOPENS the SQLite
    checkpoint file from disk — the exact read path a real process restart
    takes (in-memory caches cannot mask a persistence failure)."""
    from codegraph_sentinel import agent_graph

    stale = [k for k in agent_graph._SAVERS if k[0] == config.CHECKPOINT_PATH]
    for key in stale:
        cm, _ = agent_graph._SAVERS.pop(key)
        await cm.__aexit__(None, None, None)


async def test_conversation_survives_process_restart(gw_ingested, mcp_app):
    thread = tid("demo")
    q1 = "Which repository module matters most?"
    first_model = fake_model(
        AIMessage("The architecture graph marks validate_identity as the hub module.")
    )
    first = await run_turn(
        gw_ingested, mcp_app, q1, thread_id=thread, model=first_model
    )
    assert first["aborted"] is False
    assert len(first["messages"]) == 2  # human + agent, fresh thread

    # --- simulated process teardown: gateway closed, checkpoint handle
    # dropped, then reopen the endpoint and build a brand-new graph.
    gw_ingested.close()
    await _drop_checkpoint_handle()
    gw2 = GraphGateway.open()
    set_gateway(gw2)  # tools now read through the reopened handle
    try:
        second_model = fake_model(
            AIMessage("We were discussing the architecture graph and its hub module.")
        )
        reply = await run_turn(
            gw2, mcp_app, "What were we discussing?", thread_id=thread, model=second_model
        )
        assert reply["aborted"] is False
        contents = [str(m.content) for m in reply["messages"]]
        # Restored from checkpoint: turn 1's pair + turn 2's pair — NOT a
        # cold-start two-message thread.
        assert len(reply["messages"]) == 4, "prior turn missing from checkpoint"
        assert contents[0] == q1
        assert "validate_identity as the hub" in contents[1]
        assert "graph" in contents[-1].lower()
    finally:
        gw2.close()


async def test_memory_rule_goes_beyond_conversation(gw_ingested, mcp_app):
    recorded = graph_tools.record_memory_rule(
        gw_ingested, "validate_identity", "validate_identity is load-bearing"
    )
    assert recorded["rule"] == "validate_identity is load-bearing"

    # --- restart: close, reopen, FULL re-ingestion of the repo (ingest is
    # MERGE-idempotent; Memory nodes are never deleted by it).
    gw_ingested.close()
    gw2 = GraphGateway.open()
    set_gateway(gw2)
    try:
        ingest(gw2)
        rules = graph_tools.get_memory_rules(gw2, "validate_identity")
        assert len(rules) == 1, "memory rule must survive close/reopen + re-ingest"
        assert rules[0]["rule"] == "validate_identity is load-bearing"

        # Through the agent path too: a BRAND-NEW thread (no checkpoint
        # history at all) still sees the rule — proof the memory is
        # graph-native, not conversation state.
        model = fake_model(
            tool_call_msg("get_memory_rules", target_func="validate_identity"),
            AIMessage("A recorded rule blocks refactors of validate_identity."),
        )
        state = await run_turn(
            gw2, mcp_app, "Refactor validate_identity", thread_id=tid("mem"), model=model
        )
        assert state["aborted"] is False
        tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
        assert tool_msgs, "get_memory_rules was never executed"
        assert "load-bearing" in str(tool_msgs[-1].content)
        assert "DB_UNAVAILABLE" not in str(tool_msgs[-1].content)
    finally:
        gw2.close()
