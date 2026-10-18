"""Pure policy functions over the graph — ALL Cypher lives in this module.

Pinned queries (plan § Saved-for-reference): blast radius (corrected — direct
callers included), centrality write, memory rule check. The LLM never sees or
writes query text; it only ever chooses which tool to call.
"""

from __future__ import annotations

from datetime import datetime, timezone

from codegraph_sentinel import config
from codegraph_sentinel.graph_gateway import GraphGateway

# Plan-pinned centrality query (pinned verbatim).
CENTRALITY_QUERY = """
CALL algo.pageRank('Function', 'CALLS')
YIELD node, score
SET node.pagerank = score
RETURN node.name, node.pagerank
ORDER BY score DESC LIMIT {n}
"""

# Plan-pinned blast radius (corrected variant: direct callers stay in scope —
# Review Focus #2). {hops} comes from config.MAX_HOPS.
# Ruling: this FalkorDB build rejects `extract(...)` ("does not currently
# support extract"); the modern list-comprehension form is semantically
# identical — same nodes, same order.
BLAST_RADIUS_QUERY = """
MATCH path = (caller:Function)-[:CALLS*1..{hops}]->(target:Function {{name: $target_func}})
RETURN caller.name, [n IN nodes(path) | n.name] AS dependency_chain
"""


class AmbiguousTargetException(Exception):
    """Raised when a simple function name matches more than one :Function node
    and no filepath disambiguates (plan Review Focus #1)."""


def _count_functions(gateway: GraphGateway, name: str) -> int:
    rows = gateway.query(
        "MATCH (f:Function {name: $n}) RETURN count(f)",
        {"n": name},
        read_only=True,
    ).result_set
    return int(rows[0][0])


def _resolve(gateway: GraphGateway, target_func: str) -> int:
    """Ambiguity gate shared by every name-matching reader."""
    count = _count_functions(gateway, target_func)
    if count > 1:
        raise AmbiguousTargetException(
            f"{target_func!r} matches {count} functions; disambiguate by filepath"
        )
    return count


def get_function_names(gateway: GraphGateway) -> list[str]:
    rows = gateway.query(
        "MATCH (f:Function) RETURN f.name ORDER BY f.name", read_only=True
    ).result_set
    return [r[0] for r in rows]


def get_top_centrality(gateway: GraphGateway, n: int = 5) -> list[tuple[str, float]]:
    rows = gateway.query(
        # re-run of the pinned write: refreshes pagerank then reads top-n
        CENTRALITY_QUERY.format(n=int(n)),
        read_only=False,
    ).result_set
    return [(str(name), float(score)) for name, score in rows]


def get_function_pagerank(gateway: GraphGateway, target_func: str) -> float | None:
    _resolve(gateway, target_func)
    rows = gateway.query(
        "MATCH (f:Function {name: $n}) RETURN f.pagerank",
        {"n": target_func},
        read_only=True,
    ).result_set
    if not rows or rows[0][0] is None:
        return None
    return float(rows[0][0])


def get_blast_radius(gateway: GraphGateway, target_func: str) -> dict:
    """{"chains": [{"caller", "chain": [names], "hops"}, ...]} — hops = len(chain)-1."""
    _resolve(gateway, target_func)
    query = BLAST_RADIUS_QUERY.format(hops=int(config.MAX_HOPS))
    rows = gateway.query(query, {"target_func": target_func}, read_only=True).result_set
    chains = []
    for caller, chain in rows:
        chain = list(chain)
        chains.append({"caller": str(caller), "chain": [str(c) for c in chain], "hops": len(chain) - 1})
    return {"chains": chains}


def get_memory_rules(gateway: GraphGateway, target_func: str) -> list[dict]:
    _resolve(gateway, target_func)
    rows = gateway.query(
        """
        MATCH (f:Function {name: $n})-[:HAS_RULE]->(m:Memory)
        RETURN m.rule, m.timestamp
        ORDER BY m.timestamp
        """,
        {"n": target_func},
        read_only=True,
    ).result_set
    return [{"rule": str(rule), "timestamp": str(ts)} for rule, ts in rows]


def record_memory_rule(gateway: GraphGateway, target_func: str, rule: str) -> dict:
    count = _resolve(gateway, target_func)
    if count == 0:
        raise ValueError(f"unknown function: {target_func!r}")
    timestamp = datetime.now(timezone.utc).isoformat()
    gateway.query(
        """
        MATCH (f:Function {name: $n})
        CREATE (f)-[:HAS_RULE]->(:Memory {rule: $rule, timestamp: $ts})
        """,
        {"n": target_func, "rule": rule, "ts": timestamp},
    )
    return {"rule": rule, "timestamp": timestamp}