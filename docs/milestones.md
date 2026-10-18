# CodeGraph Sentinel — Milestone Status

**Updated:** 2026-10-18 · **Implementation plan:** [superpowers/plans/2026-10-17-codegraph-sentinel.md](superpowers/plans/2026-10-17-codegraph-sentinel.md)

**Current state:** ALL milestones complete (A–D8) — **45/45 tests passing**; kill-switch demo both banners exit 0; live E2E scenario 5/5 PASS; CLI verified against the live model.

---

## Environment (as built)

```bash
# 1. FalkorDB server (once per machine session)
docker run -d --name sentinel-falkordb -p 6379:6379 falkordb/falkordb:latest

# 2. Python env — uv-managed virtualenv, isolated from system Python
uv sync                      # creates .venv, resolves uv.lock

# 3. Ingest the mock repo once, then verify
.venv/Scripts/python.exe -m codegraph_sentinel.ingest
.venv/Scripts/python.exe -m pytest tests/     # → 45 passed
```

Connection settings are env-overridable: `FALKOR_HOST` (default `127.0.0.1`), `FALKOR_PORT` (default `6379`), `SENTINEL_MODEL` (default `nvidia/nemotron-3.5-super-vl-120b-a12b`; key via `NVIDIA_API_KEY` in `.env`).

---

## Milestone A — Ingestion ✅ (Tasks A1–A3 complete)

| Task | Delivered | Receipts |
|---|---|---|
| **A1** scaffold + config + gateway | [pyproject.toml](../pyproject.toml), [config.py](../src/codegraph_sentinel/config.py), [graph_gateway.py](../src/codegraph_sentinel/graph_gateway.py) — `GraphUnavailableError`/`GraphQueryError`, singleton-per-endpoint guard, `ping()` raises never returns False | `tests/test_gateway.py` → **6/6** |
| **A2** fixtures + tree-sitter ingest | [mock_repo/](../mock_repo/) (5 files, 7 functions, 5 call edges: hub `validate_identity`, isolated leaf `format_timestamp`, chain `run_job → load_tenant → validate_identity`), [ingest.py](../src/codegraph_sentinel/ingest.py) — idempotent two-pass writes + PageRank calibration round | `tests/test_ingest.py` → **7/7** (incl. hub-highest + threshold-pin, leaf isolation, idempotency) |
| **A3** pinned policy functions | [graph_tools.py](../src/codegraph_sentinel/graph_tools.py) — `get_function_names`, `get_top_centrality`, `get_function_pagerank`, `get_blast_radius`, `get_memory_rules`, `record_memory_rule`, `AmbiguousTargetException` | `tests/test_graph_tools.py` → **9/9** (direct callers in blast radius, order-exact multi-hop chain, rules persist across close/reopen) |

Verified end-to-end: deleting the graph and re-running `python -m codegraph_sentinel.ingest` rebuilds it (`5 files, 7 functions, 5 call edges`, exit 0).

---

## Milestone B — MCP Tool Layer ✅ (Task B4 complete)

| Task | Delivered | Receipts |
|---|---|---|
| **B4** five parameterized tools + adapter | [mcp_server.py](../src/codegraph_sentinel/mcp_server.py) — FastMCP app, exactly five `@mcp.tool()` JSON tools; single `_payload()` exception policy maps `GraphUnavailableError → {"status":"DB_UNAVAILABLE"}` and ambiguity → `{"status":"ambiguous_target"}` (payloads, never exceptions); [mcp_tools_adapter.py](../src/codegraph_sentinel/mcp_tools_adapter.py) — in-memory `fastmcp.Client` → LangChain `StructuredTool`s | `tests/test_mcp_server.py` → **8/8** (tool list, payload shapes, DB_UNAVAILABLE mapping, rule roundtrip, adapter discovery + invocation) |

---

## Milestone C — LangGraph Agent ✅ (Tasks C5–C6 complete)

| Task | Delivered | Receipts |
|---|---|---|
| **C5** gated StateGraph + policy agent | [agent_graph.py](../src/codegraph_sentinel/agent_graph.py) — `SentinelState`, async `build_graph` (ledgered deviation: fastmcp in-memory client is async-only), `run_turn`; `START → db_gate → agent → post_gate → END` + `db_gate → post_gate` abort edge. Pre-LLM `db_gate` (liveness → `DB_OFFLINE`; population + rank completeness → `GOVERNANCE_EMPTY_GRAPH`), `post_gate` anti-bypass overwrite, model built lazily so abort paths spend **zero tokens**. [policy.py](../src/codegraph_sentinel/policy.py) — pinned `DECISION_POLICY` (7 clauses, zero query vocabulary, threshold read from `config.PAGERANK_BLOCK_THRESHOLD` per ruling #3), `block_summary`, `DB_OFFLINE_REFUSAL`, `EMPTY_GRAPH_REFUSAL`, `UNKNOWN_FUNCTION_REFUSAL`, `BYPASS_REFUSAL` | `tests/test_agent_graph.py` → **8/8** (offline-DB 0-token abort, alive proceeds, `DB_UNAVAILABLE` payload refusal, empty + unranked-graph abort, pasted-code bypass override, unknown-function refusal, policy vocabulary/threshold pin) |
| **C6** cross-session persistence | Checkpointer wired into `build_graph` on `config.CHECKPOINT_PATH`, stable `thread_id` through `run_turn`; `_SAVERS` keyed by `(path, loop)` (ledger: pytest-asyncio runs each test in a fresh loop). Graph-native `:Memory` rules proven independent of the checkpointer | `tests/test_persistence.py` → **2/2** (conversation restored from disk after simulated restart — fresh thread = 2 messages vs restored thread = 4; memory rule survives close/reopen + full re-ingestion, visible in a brand-new thread) |

Bugs caught test-first during C5: cross-event-loop `aiosqlite` reuse (`no active connection` → loop-keyed cache) and `GenericFakeChatModel.bind_tools` raising `NotImplementedError` (test spy overrides).

Model integration (live-verified): `config.build_model()` → `ChatNVIDIA` from `.env` (`NVIDIA_API_KEY` + `SENTINEL_MODEL`); plain invoke and `bind_tools` structured tool-calls both confirmed against the provider.

---

## Rulings & deviations from the plan (ledger)

1. **Backend swap — `falkordblite` → official `falkordb` client + Dockerized FalkorDB server** *(user decision)*. `falkordblite` 0.10.0 declares macOS/POSIX-only support and fails to build on this Windows host. Consequences:
   - `config.DB_PATH` (`./data/sentinel_graph.rdb`) → `FALKOR_HOST`/`FALKOR_PORT`.
   - Gateway singleton is now keyed **per `(host, port)` per process** instead of per file path.
   - **Kill B semantics change:** "deleted `.rdb` recreated empty" becomes "graph dropped/flushed on a live server" (e.g. `GRAPH.DELETE`/`FLUSHALL`) — the `GOVERNANCE_EMPTY_GRAPH` population check still fires identically, but the Milestone D test must reproduce it this way.
   - Kill A (transport dead) is unchanged in spirit: unreachable endpoint / closed gateway ⇒ `GraphUnavailableError`.
   - Data is ephemeral container state; re-ingest after container recreation (README already treats ingest as bootstrap).
2. **`mock_repo/` at repo root** — plan File Structure and `SRC_PATH` say root; Task A2's `src/codegraph_sentinel/mock_repo/` disagreed. Root wins (2 sources + the executable consumer).
3. **`PAGERANK_BLOCK_THRESHOLD` calibrated 0.85 → 0.20.** This FalkorDB build normalizes PageRank so scores sum to 1.0 across all 7 functions: hub `validate_identity` = **0.248**, runner-up = 0.090, leaf = 0.063. The pinned 0.85 assumed a max-score normalization; plan Review Focus #3 names the threshold (never the gate logic) as the sanctioned knob. `test_pagerank_hub_is_highest` pins hub-highest **and** hub > threshold. ~~TODO Milestone C~~ **Resolved in C5:** `policy.DECISION_POLICY` reads `config.PAGERANK_BLOCK_THRESHOLD` (pinned by `test_policy_has_no_query_vocabulary`); README's "0.85" prose still needs the Milestone D doc refresh.
4. **`extract(...)` → `[n IN nodes(path) | ...]`** in the pinned blast-radius query: this FalkorDB rejects `extract` ("does not currently support extract"). Semantically identical, same node order.
5. **Eager connect:** the `falkordb` client connects at construction, so an unreachable endpoint raises `GraphUnavailableError` from `GraphGateway.open()` rather than `ping()` — same error type, kill-switch contract intact; both shapes are test-pinned.
6. **`build_graph` is async** *(C5 ledger)* — the plan types it sync; the fastmcp in-memory `Client` session is async-only, so the MCP session is entered for the graph's lifetime and released by `close_graph`. `run_turn` hides this from callers.
7. **Model provider → NVIDIA** *(user decision)*: `langchain-nvidia-ai-endpoints` + `python-dotenv`; temperature defaults to 0 (reproducible governance decisions), overridable via `SENTINEL_TEMPERATURE`.
8. **No commits made** — plan's per-task commit steps were left for you (repo policy: agent doesn't stage/commit unasked; `main` has no commits yet). All work is untracked/unstaged and ready to review.
9. **Kill B reproduction on Dockerized FalkorDB** *(D7 ledger)*: `GRAPH.DELETE <name>` removes the graph, but the key then rejects ALL queries with "Invalid graph operation on empty key" (even from a fresh client) — using it alone would abort via liveness, not the population check. The faithful "deleted file recreated empty" shape is `GRAPH.DELETE` + one trivial `CREATE` write: the graph comes back EMPTY (0 functions), ping passes, and only the population/rank check catches it. `tests/test_kill_switch.py` and `scripts/kill_switch_test.py` both use this shape and re-ingest afterward to leave the shared server clean.
10. **E2E harness retries provider flakes; the agent never does** *(D8 ledger)*: `scripts/e2e_scenario.py._turn` retries a turn up to 3× on provider/network errors because a live demo must survive upstream 5xx/timeouts. This tolerance lives ONLY in the demo harness — inside the agent, a `DB_UNAVAILABLE` payload or gate abort must refuse, never retry (that's the product contract, pinned in C5/D7 tests).

---

## Milestone D — Kill-switch, CLI, E2E (Tasks D7–D8)

### D7 — Kill-switch proof ✅

| Task | Delivered | Receipts |
|---|---|---|
| **D7** kill-switch tests + demo | [tests/test_kill_switch.py](../tests/test_kill_switch.py) — happy-path block renders live pagerank + all 3 dependency chains from tool payloads (the block is real, so the kills make a difference); Kill A (closed handle → `DB_OFFLINE`); Kill B (`GRAPH.DELETE` + placeholder write recreates the graph EMPTY on a live server — ping passes, only the population check catches it → `GOVERNANCE_EMPTY_GRAPH`). Both kills pin `call_count == 0` + no code token. [scripts/kill_switch_test.py](../scripts/kill_switch_test.py) — standalone demo, both banners, checkmark per pin, exit 0, keyless (gate aborts before model construction); re-ingests at the end to leave the server clean | `tests/test_kill_switch.py` → **3/3**; `python scripts/kill_switch_test.py` → both 🚫 banners + PASS, exit 0 |

Kill B shape verified on this FalkorDB build (ledger entry #9 below): `GRAPH.DELETE` alone leaves the key unqueryable ("Invalid graph operation on empty key" — would trip liveness instead of population), so the faithful "recreated empty" reproduction is `GRAPH.DELETE` + a trivial write, after which ping passes and the population check is the only defense.

### D8 — CLI REPL + E2E scenario + docs ✅

| Task | Delivered | Receipts |
|---|---|---|
| **D8** judges' entrypoints | [cli.py](../src/codegraph_sentinel/cli.py) — plain `input()` loop over `run_turn` (injectable inputs/model/gateway for tests), `python -m codegraph_sentinel.cli`; [scripts/e2e_scenario.py](../scripts/e2e_scenario.py) — the 5 judge scenarios on ONE `thread_id` with the live model, PASS/FAIL per line, non-zero exit on any failure, graph-state verification for the memory scenarios (not chat text), one retry on transient provider errors (harness-only — the agent's kill layers never retry); [tests/test_cli.py](../tests/test_cli.py) — wiring only (two piped inputs → exit 0 + both turns in the thread checkpoint; empty session spends zero tokens); README + `.env.example` refreshed (uv + Docker runbook, configured threshold, NVIDIA provider) | `tests/test_cli.py` → **2/2**; `python scripts/e2e_scenario.py` → **PASS (5/5)**, exit 0 with the live NVIDIA model; `python -m codegraph_sentinel.cli` piped session → correct 7-function answer, exit 0 |

E2E verification notes (ledger entry #10): the live run initially hit provider flakes (socket timeouts + intermittent 500s from the NVIDIA endpoint). Diagnosis: short calls succeeded while scenario turns failed at a *moving* position each run — transient provider degradation, not a payload defect (scenario 5 in isolation passed cleanly). Fixed at the cause where it was ours: a `TypeError` in the script's rule-delta check, plus harness-level retry (3 attempts, linear backoff). Clean run: all 5 PASS, exit 0.

### Definition of done for the plan ✅
Full suite green (45 tests) ✅, `kill_switch_test.py` prints both banners with 0 model tokens ✅, `e2e_scenario.py` exits 0 (5/5) ✅, CLI live-verified ✅. Remaining for the judge: run the README commands from a cold start (docker + uv sync + ingest).
