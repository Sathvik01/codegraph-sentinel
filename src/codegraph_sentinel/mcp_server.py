"""FastMCP application: five parameterized graph tools, in-memory transport.

All Cypher lives in `graph_tools.py` behind these thin wrappers — the LLM
never sees or writes query text. Outages and ambiguity map to deterministic
JSON payloads (never exceptions), so a mid-conversation DB death reads
identically every time: `{"status": "DB_UNAVAILABLE"}`.

The server also stays stdio-runnable (`python -m ... ` / `mcp.run`) for a
separate-process demo — the main flow uses the in-memory client because
FalkorDB server access is owned by one gateway per process.
"""

from __future__ import annotations

import functools
import json
from typing import Callable

from fastmcp import FastMCP

from codegraph_sentinel import graph_tools
from codegraph_sentinel.graph_gateway import GraphGateway, GraphUnavailableError
from codegraph_sentinel.graph_tools import AmbiguousTargetException

_gateway: GraphGateway | None = None


def set_gateway(gateway: GraphGateway | None) -> None:
    """Wire the gateway the tools read through (CLI startup, test fixtures)."""
    global _gateway
    _gateway = gateway


def _active_gateway() -> GraphGateway:
    if _gateway is None:
        raise GraphUnavailableError("no gateway wired into the MCP server")
    return _gateway


def _payload(fn: Callable[..., dict]) -> Callable[..., str]:
    """Body dict -> JSON string; the module's single exception policy:
    ambiguity -> ambiguous_target payload, outage -> DB_UNAVAILABLE payload."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> str:
        try:
            body = fn(*args, **kwargs)
        except AmbiguousTargetException:
            body = {"status": "ambiguous_target"}
        except GraphUnavailableError:
            body = {"status": "DB_UNAVAILABLE"}
        return json.dumps(body)

    return wrapper


mcp = FastMCP("CodeGraph Sentinel")


@mcp.tool()
@_payload
def list_functions() -> str:
    """List every function name known to the architecture graph, sorted.

    Use first when the user asks what exists in the repository.
    """
    return {"status": "ok", "functions": graph_tools.get_function_names(_active_gateway())}


@mcp.tool()
@_payload
def get_centrality(target_func: str) -> str:
    """PageRank centrality score of one function — the criticality signal.

    Compare the returned pagerank against the block threshold before any
    refactor. Returns status ok + pagerank float, or status unknown_function
    when the name is not in the graph.
    Args:
        target_func: simple function name as parsed from source
    """
    pagerank = graph_tools.get_function_pagerank(_active_gateway(), target_func)
    if pagerank is None:
        return {"status": "unknown_function"}
    return {"status": "ok", "target_func": target_func, "pagerank": pagerank}


@mcp.tool()
@_payload
def get_blast_radius(target_func: str) -> str:
    """Everything that breaks if a function changes: caller dependency chains.

    Returns status ok with `chains` (caller, chain, hops) and
    `total_dependencies`; quote these chains as evidence in refusals.
    Args:
        target_func: simple function name as parsed from source
    """
    result = graph_tools.get_blast_radius(_active_gateway(), target_func)
    chains = result["chains"]
    return {"status": "ok", "chains": chains, "total_dependencies": len(chains)}


@mcp.tool()
@_payload
def get_memory_rules(target_func: str) -> str:
    """Permanent architectural rules already recorded against a function.

    Any returned rule blocks a refactor regardless of pagerank.
    Args:
        target_func: simple function name as parsed from source
    """
    return {"status": "ok", "rules": graph_tools.get_memory_rules(_active_gateway(), target_func)}


@mcp.tool()
@_payload
def record_architectural_rule(target_func: str, rule: str) -> str:
    """Persist an architectural decision as graph memory (survives re-ingest).

    Call after an ALLOW decision to record why the change was cleared, or to
    freeze a manual governance constraint on a function.
    Args:
        target_func: simple function name as parsed from source
        rule: the permanent constraint or clearance note, one sentence
    """
    if graph_tools.get_function_pagerank(_active_gateway(), target_func) is None:
        return {"status": "unknown_function"}
    recorded = graph_tools.record_memory_rule(_active_gateway(), target_func, rule)
    return {"status": "ok", "recorded": True, "timestamp": recorded["timestamp"]}


if __name__ == "__main__":
    mcp.run(transport="stdio")