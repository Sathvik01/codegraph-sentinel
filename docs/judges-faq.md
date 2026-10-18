# CodeGraph Sentinel — Judges' Technical FAQ

One page. Answers in plain language, with receipts from the test suite.

## Q1. Why is FastMCP running *inside* the agent process? Isn't MCP supposed to be out-of-band?

MCP is a protocol (tool discovery + typed, parameterized calls), not a transport requirement. We run it over FastMCP's documented **in-memory transport** for one hard technical reason: FalkorDB access is owned by exactly one gateway per `(host, port)` per process (a singleton guard, test-pinned) — a second process on the same endpoint is a bug, not a feature. We chose data integrity over ceremony.

What the judges still get: the **full MCP protocol boundary is exercised**. The agent discovers the runtime tool registry via `Client.list_tools()` and invokes every operation through `Client.call_tool(name, args)` — it cannot see or bypass the five parameterized tools. The FastMCP server module stays independently runnable over stdio (`mcp.run(transport="stdio")`) for a separate-process demo against a throwaway database file.

## Q2. How does the kill-switch work, and why does it matter for indispensability?

Three deterministic layers, all pinned by tests:

1. **Pre-flight gate (`db_gate` node):** before any LLM invocation, the LangGraph agent verifies the database is alive (`RETURN 1`) AND that the governance graph is populated and fully ranked (zero `Function` nodes, or any missing `pagerank`, aborts with `GOVERNANCE_EMPTY_GRAPH` — important because a graph dropped and recreated empty on a live server would otherwise pass a bare liveness check while governing nothing). Either failure routes to an abort node that emits the refusal itself — **zero model tokens are spent**. The test asserts `fake_llm.call_count == 0`, and `scripts/kill_switch_test.py` demonstrates both kills end-to-end.
2. **Runtime payload policy:** if the database dies mid-conversation, graph tools return `{"status": "DB_UNAVAILABLE"}` payloads instead of exceptions, and the policy forbids the agent from emitting any code in response.
3. **Post-hoc evidence check (`post_gate`):** if a turn's final message contains code but the turn never made a successful graph-tool call, the message is deterministically overwritten with a refusal — the agent can't sneak an un-grounded answer from chat context alone.

What this proves: the agent has no internal code-generation capability it can fall back on. It *cannot* answer a refactor question without the graph — by construction, not by prompt courtesy. Swap FalkorDB off and the product degrades to a refusal message, not to a generic coding assistant.

## Q3. Why is FalkorDB *indispensable* — wouldn't vector RAG work?

It wouldn't, for the three capabilities the product is built on:

- **Multi-hop traversal:** blast radius is `(...)-[:CALLS*1..5]->(target)` over the dependency graph — a sparse-matrix product ($\mathbf{A}^k \cdot \mathbf{x}$) under GraphBLAS. A vector database retrieves *similar texts*; it cannot enumerate *transitive callers*, and the proof shown to the user (the exact dependency chain) comes from the traversal result.
- **Centrality as a decision variable:** `algo.pageRank` is what identifies a function as load-bearing (score above the calibrated block threshold — currently 0.20 on this FalkorDB build — blocks the change; see the calibration note in [milestones.md](milestones.md)). Similarity scores have no structural meaning; PageRank does.
- **Graph-native memory:** refusals are written as `(:Function)-[:HAS_RULE]->(:Memory)` — permanent architectural constraints that survive re-ingestion — a plain vector store can't attach a durable, typed constraint *to a node* and gate on it later.

Same test either way: pull FalkorDB out, the reasoning breaks at all three operations. That is the point.

## Q4. The LLM never writes Cypher?

Never. All Cypher lives in `graph_tools.py` behind five tools (`get_centrality`, `get_blast_radius`, `get_memory_rules`, `record_architectural_rule`, `list_functions`). The system prompt contains zero query vocabulary; the LLM chooses *which tool to ask*, not *how to ask it*. This is deliberate: parameterized, reviewed Cypher is audited and fast; generated Cypher is neither.

## Q5. Where does "memory across sessions" actually live?

Two layers, with different lifetimes:

| Layer | Storage | Survives | Purpose |
|---|---|---|---|
| Conversation state | SQLite checkpointer (`thread_id`) | Process restarts | "What were we discussing?" |
| Architectural rules | `(:Memory)` nodes in the graph | Full re-ingestion | "identity validation is load-bearing" still blocks |

The second is the important one for governance: rules are *data in the DB that gates future reasoning* — which is exactly the hackathon's persistent-memory requirement.

## Q6. What auditable proof does the agent show when it blocks a change?

It quotes the graph, verbatim and ordered: (1) the PageRank float that tripped the threshold, (2) every dependency chain returned by the traversal — including the multi-hop exemplar `run_job → load_tenant → validate_identity`, (3) any memory rules with timestamps. Each claim is re-checkable in the DB in one command; nothing is hallucinated narrative.

## Receipts (how to verify each claim in <5 min)

- Kill-switch: `pytest tests/test_kill_switch.py -v` then `python scripts/kill_switch_test.py`
- Full agent flow: `python scripts/e2e_scenario.py` → 5 PASS lines (including the pasted-code bypass scenario)
- Multi-hop proof live: REPL prompt "What's the blast radius of validate_identity?"
