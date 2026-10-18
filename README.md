# CodeGraph Sentinel 🛡️

**Graph Hacks: Context for AI Agents** submission — CodeGraph Sentinel is a **graph-native architecture governance agent**: it refuses refactors of structurally critical functions, proves every refusal with multi-hop traversal output, and remembers its verdicts forever.

### Elevator pitch

When an AI assistant decides *how risky* a code change is, vector RAG retrieves flat text chunks — it can see that two functions look similar, but not that one of them is six calls away from load-bearing. CodeGraph Sentinel parses a codebase into an AST, stores the structure — not the text — in FalkorDB, and answers the only question that matters before touching code: **what breaks if this function changes?** It walks the dependency graph up to five hops (`A^k · x` under GraphBLAS), scores every function with PageRank, and if your target is structurally critical, it blocks the change — quoting the exact dependency chain as evidence. Then it writes the verdict into the graph as a permanent rule, so every future session inherits the constraint.

**Try to refactor a central function: you get refused, with proof. Try it again next week, in a fresh session: still refused — and no LLM ever wrote the rule from imagination; the graph holds it.**

---

## Architecture

One Python process → FastMCP tools (in-memory transport) → LangGraph agent → FalkorDB server (Docker).

```
┌──────────────────────────── one Python process ───────────────────────────┐
│                                                                           │
│  User ──► cli.py REPL (thread_id → SQLite checkpointer)                   │
│              │                                                            │
│              ▼                                                            │
│  ┌── db_gate ──┤ pre-flight, deterministic, pre-LLM:                      │
│  │   DB alive?  graph populated AND fully pagerank-ranked?                 │
│  │   no → abort: refusal text with ZERO model tokens                       │
│  ▼                                                                        │
│  ┌── agent ─────────────────────────────────────────────────────────┐     │
│  │ create_agent(model, tools, system_prompt)                        │     │
│  │   └ FastMCP tool call ─ graph_tools.py (all Cypher lives here)   │     │
│  │       └ GraphGateway ─ FalkorDB server (Docker, graph codesentinel)│    │
│  │   tool says DB_UNAVAILABLE ⇒ agent refuses, writes no code       │     │
│  └──────────────────────────────────────────────────────────────────┘     │
│              ▼                                                            │
│  ┌── post_gate ─┤ code appears with no graph evidence? overwrite with    │
│  │             │ refusal — the anti-bypass, deterministic                 │
│  └──────────► reply                                            │         │
│                                                                           │
│  ingest.py: tree-sitter parse of mock_repo/ → graph, then PageRank write  │
└───────────────────────────────────────────────────────────────────────────┘

Graph "codesentinel":
  (:File {filepath, extension})─[:DEFINES]->(:Function {name, pagerank})
  (:Function)-[:CALLS]->(:Function)
  (:Function)-[:HAS_RULE]->(:Memory {rule, timestamp})
```

**Why in-process MCP?** FalkorDB access is owned by exactly one gateway per `(host, port)` per process (singleton guard) — a second process on the same endpoint is a bug, not a feature. We still run the MCP server over FastMCP's documented **in-memory transport**: the agent discovers tools via `list_tools()` and calls them via `call_tool(name, args)` — the full protocol boundary, exercised. The server stays stdio-runnable (`python -m codegraph_sentinel.mcp_server`) for a separate-process demo.

## Why FalkorDB is indispensable (the hackathon's core constraint)

Vector RAG cannot do any of the three operations this product is built on:

1. **Multi-hop blast radius.** `MATCH path = (caller:Function)-[:CALLS*1..5]->(target …)` enumerates *transitive* callers — a sparse-matrix multiplication. The evidence shown to the user (the exact chain, e.g. `run_job → load_tenant → validate_identity`) is a traversal result, not a similarity score.
2. **Centrality as a decision variable.** `CALL algo.pageRank('Function','CALLS')` is what makes "critical" measurable (score above the calibrated block threshold ⇒ BLOCK — currently **0.20**, see [ruling on normalization](docs/milestones.md)). Similarity has no structural meaning; PageRank does.
3. **Graph-native persistent memory.** Verdicts are stored as `(:Function)-[:HAS_RULE]->(:Memory)` nodes — typed constraints attached *to a node*, surviving full re-ingestion, gating every future session.

**And the kill-switch proves it's not rhetoric.** Three deterministic layers, all pinned by tests:

| Layer | Mechanism | Guarantee |
|---|---|---|
| `db_gate` (pre-LLM) | liveness ping **and** graph-population check (catches a graph dropped and recreated empty on a live server) | dead/empty DB ⇒ refusal with **0 model tokens** |
| tool payload policy | graph tools return `{"status":"DB_UNAVAILABLE"}`, the policy forbids any code in response | mid-conversation death ⇒ refusal |
| `post_gate` override | code in the final message with no successful graph-tool call that turn ⇒ message overwritten with a refusal | the agent can't reason from chat context alone |

Run the proof: `python scripts/kill_switch_test.py` → both banners printed, exit 0:

```
🚫 FalkorDB offline → code generation aborted (0 model tokens)
🚫 Governance graph empty → code generation aborted (0 model tokens)
```

Pull FalkorDB out and CodeGraph Sentinel isn't a degraded chatbot — it's a wall.

## What the LLM is allowed to do

- Choose **which** of five graph tools to call: `get_centrality`, `get_blast_radius`, `get_memory_rules`, `record_architectural_rule`, `list_functions`.
- Compose the final answer from tool results.

What it may never do: write Cypher (all queries are parameterized, reviewed code in `graph_tools.py` — the system prompt contains zero query vocabulary), emit code without graph evidence, or improvise around an `unknown_function` result. Prompt injection through function names, tool payloads, or file contents is untrusted input by policy clause.

## Tech stack

Dockerized **FalkorDB** server (SuiteSparse:GraphBLAS) via the official `falkordb` client · `fastmcp 4.1.0` · LangChain 1.x `create_agent` + LangGraph `StateGraph` · `langgraph-checkpoint-sqlite` (cross-session conversation memory) · `tree-sitter` + `tree-sitter-python` (AST ingestion) · **NVIDIA NIM** LLM (`langchain-nvidia-ai-endpoints`) · Python ≥ 3.12 · `uv` for env management.

Two memory layers, two lifetimes: **conversation state** (SQLite checkpoint, survives process restarts) and **architectural rules** (graph `:Memory` nodes, survive full re-ingestion). Governance lives in the second.

## Judge demo

```bash
# 0. prerequisites
docker run -d --name sentinel-falkordb -p 6379:6379 falkordb/falkordb:latest
cp .env.example .env            # then set NVIDIA_API_KEY

# 1. one-time bootstrap
uv sync                         # creates .venv, installs the project
uv run python -m codegraph_sentinel.ingest   # parses mock_repo/ into the graph

# 2. the proof of indispensability (no API key needed — gate aborts pre-LLM)
uv run python scripts/kill_switch_test.py    # 2 banners, exit 0

# 3. the full governance story, headless (live model, 5 PASS lines)
uv run python scripts/e2e_scenario.py        # exits 0

# 4. live REPL
uv run python -m codegraph_sentinel.cli
```

### The 90-second judge walk-through (REPL)

| Say | See |
|---|---|
| *"What's the blast radius of validate_identity?"* | pagerank float + dependency chains (`parse_config → …`, `run_job → load_tenant → validate_identity` …) |
| *"Refactor validate_identity"* | **BLOCKED** — pagerank above the block threshold, every dependency chain quoted verbatim |
| *"Record a permanent rule: identity validation is load-bearing."* | `HAS_RULE → Memory` node created, timestamped |
| *"Refactor format_timestamp instead"* | allowed — leaf node, zero dependents; the allow-decision is itself recorded as a rule |
| New terminal, same thread: *"Refactor validate_identity again"* | blocked from memory — frozen rule, no re-derivation |
| Kill the DB, ask again | deterministic refusal, 0 LLM tokens |

Every row above is asserted by the test suite — see the receipts in [docs/judges-faq.md](docs/judges-faq.md), and each claim re-checkable against the DB in one command.

## Repository map

| Path | What it is |
|---|---|
| `src/codegraph_sentinel/graph_gateway.py` | the single DB access point (singleton-per-endpoint guard) |
| `src/codegraph_sentinel/graph_tools.py` | all five pinned Cypher queries behind pure functions |
| `src/codegraph_sentinel/mcp_server.py` | FastMCP app — five tools, JSON payloads |
| `src/codegraph_sentinel/mcp_tools_adapter.py` | in-memory MCP client → LangChain tools |
| `src/codegraph_sentinel/agent_graph.py` | the gated LangGraph loop (`db_gate → agent → post_gate`) |
| `src/codegraph_sentinel/policy.py` | the pinned decision policy + fixed refusal strings |
| `src/codegraph_sentinel/cli.py` | the judges' REPL |
| `mock_repo/` | the five-file fixture the demo runs against |
| `scripts/kill_switch_test.py` | the indispensability proof |
| `scripts/e2e_scenario.py` | the 5-scenario headless governance story |
| `docs/judges-faq.md` | deeper technical FAQ (same facts, more detail) |
| `tests/` | every guarantee in this README is asserted somewhere in here |

## Test suite guarantees

- Blast radius includes **direct callers** and the multi-hop chain is order-exact.
- Hub function ranks strictly highest after ingest (PageRank calibration pinned, never weakened).
- Memory rules survive gateway close/reopen *and* full re-ingestion.
- Conversation state survives a simulated process restart via the SQLite checkpointer.
- Both kill modes abort deterministically with zero model calls — pinned in tests *and* demonstrated by the standalone script.
- Pasted-code turns that skip the graph are deterministically overridden.
- The CLI records every piped turn into its thread.

## Known limits (by design, for the hackathon scope)

- Mock repo of five files: the fixture proves the reasoning engine; real codebases plug in unchanged.
- PageRank normalization across FalkorDB versions isn't guaranteed [0,1]; the threshold is calibrated (currently 0.20), never the gate weakened. See [docs/milestones.md](docs/milestones.md) for the calibration numbers.
- One gateway per `(host, port)` per process: right-sized for the demo; the same gateway code points at a hosted FalkorDB for production scale-out. Graph data is ephemeral container state — re-run ingest after recreating the container.

---

*Built with: FalkorDB, FastMCP, LangGraph, tree-sitter, NVIDIA NIM. Every claim in this README is test-pinned — auditable the same way the agent's verdicts are.*
