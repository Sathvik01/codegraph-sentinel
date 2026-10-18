"""D7 kill-switch: the indispensability proof, three shapes pinned.

1. Happy gateway — the block is REAL: the refusal renders the pagerank
   float and the multi-hop dependency chain from live tool payloads
   (so the kills below make a difference, not theater).
2. Kill A — transport dead: gateway closed, ping raises. Pre-LLM gate
   aborts: DB_OFFLINE, zero model tokens, no code token in the refusal.
3. Kill B — the sneaky one (ruling #1's adapted shape): the graph is
   DROPPED on a live server (GRAPH.DELETE) and recreated empty by a
   trivial write — ping now PASSES, so only the population check can
   catch it. GOVERNANCE_EMPTY_GRAPH, zero model tokens, no code token.

All four assertions hold for BOTH killings. Models are scripted fakes —
the kill turns assert call_count == 0, so no provider key is involved.
"""

import uuid

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from codegraph_sentinel import config, graph_tools, policy
from codegraph_sentinel.agent_graph import run_turn
from codegraph_sentinel.ingest import ingest

HUB = "validate_identity"
MULTIHOP_CHAIN = "run_job -> load_tenant -> validate_identity"

CODE_TOKENS = ("```", "def ", "class ")


class FakeToolModel(GenericFakeChatModel):
    """Scripted model with a call counter (bind_tools override required —
    BaseChatModel's default raises NotImplementedError)."""

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
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _assert_no_code_token(final: str) -> None:
    for token in CODE_TOKENS:
        assert token not in final, f"kill refusal leaked code token {token!r}"


async def test_happy_gateway_block_renders_graph_evidence(gw_ingested, mcp_app):
    """The block is real: refusal quotes the live pagerank + every chain."""
    pagerank = graph_tools.get_function_pagerank(gw_ingested, HUB)
    chains = graph_tools.get_blast_radius(gw_ingested, HUB)["chains"]
    rules = graph_tools.get_memory_rules(gw_ingested, HUB)
    assert MULTIHOP_CHAIN in [" -> ".join(c["chain"]) for c in chains]

    model = fake_model(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_centrality", "args": {"target_func": HUB}, "id": "c1", "type": "tool_call"},
                {"name": "get_blast_radius", "args": {"target_func": HUB}, "id": "c2", "type": "tool_call"},
                {"name": "get_memory_rules", "args": {"target_func": HUB}, "id": "c3", "type": "tool_call"},
            ],
        ),
        AIMessage(policy.block_summary(HUB, pagerank, chains, rules)),
    )
    state = await run_turn(
        gw_ingested, mcp_app, f"Refactor {HUB}", thread_id=tid("ks-happy"), model=model
    )
    assert state["aborted"] is False  # gate passed — the block comes from evidence
    final = state["messages"][-1].content
    assert "BLOCKED" in final
    assert f"{pagerank:.4f}" in final  # the live PageRank float, rendered
    assert MULTIHOP_CHAIN in final  # the multi-hop chain, verbatim
    assert len(chains) >= 3 and all(
        " -> ".join(c["chain"]) in final for c in chains
    ), "every dependency chain must be quoted"


async def test_kill_a_transport_dead_aborts_without_llm(gw_ingested, mcp_app):
    """Kill A: closed gateway handle — ping raises, gate aborts pre-LLM."""
    model = fake_model(AIMessage("this must never be generated"))
    gw_ingested.close()
    state = await run_turn(
        gw_ingested, mcp_app, f"Refactor {HUB}", thread_id=tid("ks-killA"), model=model
    )
    assert state["aborted"] is True and state["abort_reason"] == "DB_OFFLINE"
    assert model.call_count == 0  # ZERO model tokens spent
    final = state["messages"][-1].content
    assert policy.DB_OFFLINE_REFUSAL in final
    _assert_no_code_token(final)


async def test_kill_b_empty_recreation_caught_by_population_check(gw_ingested, mcp_app):
    """Kill B: graph dropped on a LIVE server and recreated empty — ping
    passes, so only the population check stands between this and a
    hallucinated refactor."""
    model = fake_model(AIMessage("this must never be generated"))
    # Out-of-band destruction, as a DBA would: not Cypher, so it goes
    # through the raw client — the gateway's own query API is not used.
    gw_ingested._db.execute_command("GRAPH.DELETE", config.GRAPH_NAME)
    gw_ingested.graph().query("CREATE (:Placeholder {note: 'recreated empty'})")
    assert gw_ingested.ping() is True  # liveness alone is blind — the point

    state = await run_turn(
        gw_ingested, mcp_app, f"Refactor {HUB}", thread_id=tid("ks-killB"), model=model
    )
    assert state["aborted"] is True
    assert state["abort_reason"] == "GOVERNANCE_EMPTY_GRAPH"
    assert model.call_count == 0  # ZERO model tokens spent
    final = state["messages"][-1].content
    assert policy.EMPTY_GRAPH_REFUSAL in final
    _assert_no_code_token(final)

    # leave the shared server clean for every later test (fixture re-ingests,
    # but the placeholder node must not linger either)
    gw_ingested.graph().query("MATCH (n) DETACH DELETE n")
    ingest(gw_ingested)
