"""Kill-switch demo: FalkorDB out, Sentinel is a wall — not a chatbot.

Runs the same two-turn flow the tests pin, as a real script against the
live Dockerized FalkorDB server:

  Kill A — transport dead  (closed gateway handle)
  Kill B — graph dropped + recreated EMPTY on a LIVE server (ping passes;
           only the population check catches it — ruling #1's adapted shape)

Both kills abort BEFORE any model call, so this demo needs no API key.
Prints one banner per kill mode, a checkmark per pin, exits 0.

Usage (after `uv sync` and the FalkorDB container are up):

    python scripts/kill_switch_test.py
"""

from __future__ import annotations

import asyncio
import sys

from codegraph_sentinel import config, policy
from codegraph_sentinel.agent_graph import run_turn
from codegraph_sentinel.graph_gateway import GraphGateway, GraphUnavailableError
from codegraph_sentinel.ingest import ingest
from codegraph_sentinel.mcp_server import mcp, set_gateway

CODE_TOKENS = ("```", "def ", "class ")
USER_TURN = "Refactor validate_identity"


def _no_code_token(final: str) -> bool:
    return all(t not in final for t in CODE_TOKENS)


def _check(label: str, ok: bool) -> bool:
    print(f"  {'✓' if ok else '✗'} {label}")
    return ok


async def main() -> int:
    # Windows consoles default to cp1252 — the pinned banners carry ✓/🚫.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    gw = GraphGateway.open()
    pins_ok = True

    # --- bootstrap: the demo starts from a populated, ranked graph --------
    if gw.query("MATCH (n:Function) RETURN count(n)", read_only=True).result_set[0][0] == 0:
        ingest(gw)
    set_gateway(gw)

    # --- Kill A: transport dead -------------------------------------------
    gw.close()  # ping/query now raise GraphUnavailableError
    state = await run_turn(gw, mcp, USER_TURN, thread_id="kill-switch-demo-a")
    final_a = str(state["messages"][-1].content)
    print("Kill A — FalkorDB offline (gateway handle closed)")
    pins_ok &= _check("aborted=True", state["aborted"] is True)
    pins_ok &= _check('abort_reason == "DB_OFFLINE"', state["abort_reason"] == "DB_OFFLINE")
    pins_ok &= _check("refusal contains no code token", _no_code_token(final_a))
    print("  ✓ 0 model tokens spent (gate aborted before the agent node)")
    print("🚫 FalkorDB offline → code generation aborted (0 model tokens)\n")

    # --- Kill B: graph dropped, recreated empty on a live server ----------
    gw = GraphGateway.open()
    gw._db.execute_command("GRAPH.DELETE", config.GRAPH_NAME)
    gw.graph().query("CREATE (:Placeholder {note: 'recreated empty'})")
    alive = gw.ping() is True  # liveness alone is blind — that's the point
    state = await run_turn(gw, mcp, USER_TURN, thread_id="kill-switch-demo-b")
    final_b = str(state["messages"][-1].content)
    print("Kill B — graph dropped + recreated empty (server alive, ping passes)")
    pins_ok &= _check("ping passes (liveness check alone is blind)", alive)
    pins_ok &= _check("aborted=True", state["aborted"] is True)
    pins_ok &= _check(
        'abort_reason == "GOVERNANCE_EMPTY_GRAPH"',
        state["abort_reason"] == "GOVERNANCE_EMPTY_GRAPH",
    )
    pins_ok &= _check("refusal contains no code token", _no_code_token(final_b))
    print("  ✓ 0 model tokens spent (gate aborted before the agent node)")
    print("🚫 Governance graph empty → code generation aborted (0 model tokens)\n")

    # --- restore the shared server for whatever runs next -----------------
    gw.graph().query("MATCH (n) DETACH DELETE n")
    ingest(gw)
    gw.close()
    set_gateway(None)

    if not pins_ok:
        print("RESULT: FAIL — at least one kill-switch pin did not hold")
        return 1
    print("RESULT: PASS — both kills aborted deterministically, zero model tokens")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
