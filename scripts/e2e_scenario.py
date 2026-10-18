"""E2E governance story, headless: five scenarios, ONE thread_id, live model.

Runs the plan's five judge scenarios sequentially through `run_turn` on a
single thread and prints PASS/FAIL per line, exiting non-zero on any
failure:

  1. informational blast radius — >= 3 dependency chains quoted
  2. hub refactor       — BLOCKED: live pagerank + multi-hop chain quoted
  3. record rule        — a rule lands in the GRAPH (get_memory_rules grows)
  4. leaf refactor      — ALLOWED; auto-recorded clearance rule exists
  5. pasted code        — anchor centrality checked OR bypass refusal; no code

Scenarios 3/4 verify GRAPH state (not chat text) — that is the product
claim. The graph is wiped + re-ingested up front so re-runs are identical
(a recorded rule from a previous run would legitimately block scenario 4).

Usage (FalkorDB container up, NVIDIA_API_KEY set in .env):

    python scripts/e2e_scenario.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from codegraph_sentinel import config, graph_tools, policy
from codegraph_sentinel.agent_graph import _has_code, run_turn
from codegraph_sentinel.graph_gateway import GraphGateway
from codegraph_sentinel.ingest import ingest
from codegraph_sentinel.mcp_server import mcp, set_gateway

HUB = "validate_identity"
LEAF = "format_timestamp"
ANCHOR = "parse_config"

_results: list[bool] = []


def _report(n: int, label: str, ok: bool, evidence: str = "") -> None:
    _results.append(ok)
    suffix = f" — {evidence}" if evidence else ""
    print(f"{'PASS' if ok else 'FAIL'} {n}/5 {label}{suffix}")


async def _turn(gw, thread: str, text: str):
    """run_turn with up to 3 attempts on transient provider/network errors —
    a live demo must survive dropped sockets and provider 5xx flakes;
    scenario assertions are unchanged. Only this harness retries; the
    agent's own kill layers never do (a real outage must refuse, not retry)."""
    for attempt in range(1, 4):
        try:
            return await run_turn(gw, mcp, text, thread_id=thread)
        except Exception as exc:  # noqa: BLE001 - live-provider flake tolerance
            if attempt == 3:
                raise
            print(f"  (transient provider error, attempt {attempt}/3: {type(exc).__name__}: {str(exc)[:80]})")
            await asyncio.sleep(2 * attempt)


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    gw = GraphGateway.open()
    set_gateway(gw)
    thread = f"e2e-{uuid.uuid4().hex[:8]}"
    try:
        # clean, deterministic starting graph (recorded rules would block reruns)
        gw.graph().query("MATCH (n) DETACH DELETE n")
        ingest(gw)
        print(f"thread_id={thread}  graph=ingested  model={config.MODEL}\n")

        def new_messages(state, seen: int):
            return state["messages"][seen:]

        # --- 1. informational: blast radius --------------------------------
        state = await _turn(
            gw, thread, f"What's the blast radius of {HUB}?"
        )
        final = str(state["messages"][-1].content)
        chains = graph_tools.get_blast_radius(gw, HUB)["chains"]
        mentioned = sum(1 for c in chains if c["caller"] in final)
        _report(
            1,
            "informational blast radius",
            state["aborted"] is False and mentioned >= 3,
            f"{mentioned}/{len(chains)} chains quoted",
        )

        # --- 2. hub refactor is BLOCKED with evidence ----------------------
        seen = len(state["messages"])
        state = await _turn(gw, thread, f"Refactor {HUB}")
        final = str(state["messages"][-1].content)
        pagerank = graph_tools.get_function_pagerank(gw, HUB)
        hub_ok = (
            state["aborted"] is False
            and ("BLOCK" in final.upper() or "refus" in final.lower())
            and f"{pagerank:.3f}" in final
            and "run_job" in final and "load_tenant" in final and HUB in final
            and not _has_code(final)
        )
        _report(
            2,
            "hub refactor BLOCKED",
            hub_ok,
            f"pagerank {pagerank:.4f} > {config.PAGERANK_BLOCK_THRESHOLD}, multi-hop chain quoted"
            if hub_ok
            else "expected block + pagerank + chain",
        )

        # --- 3. record a permanent rule -> GRAPH write ---------------------
        seen = len(state["messages"])
        rules_before = graph_tools.get_memory_rules(gw, HUB)
        state = await _turn(
            gw,
            thread,
            "Record a permanent rule: identity validation is load-bearing on every entry path.",
        )
        final = str(state["messages"][-1].content)
        rules_after = graph_tools.get_memory_rules(gw, HUB)
        recorded = [r for r in rules_after if r not in rules_before]
        _report(
            3,
            "rule recorded in graph",
            len(rules_after) > len(rules_before),
            f"{len(recorded)} new rule(s) on {HUB}: {recorded[0]['rule'][:60]!r}" if recorded else "no graph write",
        )

        # --- 4. leaf refactor is ALLOWED with auto-recorded clearance ------
        leaf_rules_before = len(graph_tools.get_memory_rules(gw, LEAF))
        state = await _turn(gw, thread, f"OK, refactor {LEAF} instead")
        final = str(state["messages"][-1].content)
        leaf_rules = graph_tools.get_memory_rules(gw, LEAF)
        leaf_ok = (
            len(leaf_rules) > leaf_rules_before
            and "BLOCK" not in final.upper()
            and not _has_code(final)
        )
        _report(
            4,
            "leaf refactor ALLOWED",
            leaf_ok,
            f"auto-recorded clearance rule on {LEAF}" if leaf_ok else "no clearance rule recorded",
        )

        # --- 5. pasted code without an anchor is governed ------------------
        seen = len(state["messages"])
        state = await _turn(
            gw, thread, "Refactor this snippet: ```\ndef parse_config():\n    return {}\n```"
        )
        final = str(state["messages"][-1].content)
        turn = state["messages"][seen:]
        centrality_called = any(
            getattr(m, "name", None) == "get_centrality"
            for m in turn
        ) or any(
            tc["name"] == "get_centrality" and tc.get("args", {}).get("target_func") == ANCHOR
            for m in turn
            if getattr(m, "tool_calls", None)
            for tc in m.tool_calls
        )
        bypassed = policy.BYPASS_REFUSAL in final
        paste_ok = (centrality_called or bypassed) and not _has_code(final)
        _report(
            5,
            "pasted code governed",
            paste_ok,
            "anchor centrality checked, no code emitted"
            if paste_ok
            else f"centrality_called={centrality_called} bypassed={bypassed} code_leaked={_has_code(final)}",
        )

        passed = sum(_results)
        print(f"\nRESULT: {'PASS' if all(_results) else 'FAIL'} ({passed}/{len(_results)})")
        return 0 if all(_results) else 1
    finally:
        gw.close()
        set_gateway(None)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
