"""Judges' REPL: one line in, one governed answer out.

Plain `input()` loop over `run_turn` — no new frameworks (plan D8). One
stable `thread_id` per session gives the checkpointer cross-turn memory;
`db_gate` keeps every turn gated. Exits 0 on EOF/Ctrl-D/`exit`.

    python -m codegraph_sentinel.cli
"""

from __future__ import annotations

import asyncio
import sys

from codegraph_sentinel.agent_graph import run_turn
from codegraph_sentinel.graph_gateway import GraphGateway
from codegraph_sentinel.mcp_server import mcp, set_gateway

BANNER = "CodeGraph Sentinel — governance review. Type 'exit' or Ctrl-D to quit."


async def main(
    inputs=None,
    *,
    thread_id: str = "cli",
    model=None,
    gateway=None,
) -> int:
    """REPL entrypoint. `inputs` (tests) supplies lines; None reads stdin.

    Returns a process exit code: 0 on clean EOF, 1 on unexpected failure.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 consoles
    own_gateway = gateway is None
    gw = gateway if gateway is not None else GraphGateway.open()
    set_gateway(gw)
    try:
        print(BANNER)
        if inputs is None:
            inputs = iter(input, "")  # read until empty line / EOF
        for line in inputs:
            text = line.strip()
            if not text:
                continue
            if text.lower() in {"exit", "quit"}:
                break
            state = await run_turn(gw, mcp, text, thread_id=thread_id, model=model)
            print(state["messages"][-1].content)
        return 0
    finally:
        set_gateway(None)
        if own_gateway:
            gw.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
