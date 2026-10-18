"""Tree-sitter ingestion: parse mock_repo/*.py into the architecture graph.

Produces (pinned schema):
  (:File {filepath, extension})-[:DEFINES]->(:Function {name, pagerank})
  (:Function)-[:CALLS]->(:Function)
and finishes with the pinned PageRank write so the graph is immediately
governable (no separate ranking step).

Idempotent: MERGE-guarded nodes/edges — a second run changes nothing and
returns the same totals.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python as tspy
from tree_sitter import Language, Parser

from codegraph_sentinel import config
from codegraph_sentinel.graph_gateway import GraphGateway

PY_LANGUAGE = Language(tspy.language())
PARSER = Parser(PY_LANGUAGE)

# Plan-pinned centrality query (writes pagerank onto every Function node).
PAGERANK_WRITE = """
CALL algo.pageRank('Function', 'CALLS')
YIELD node, score
SET node.pagerank = score
RETURN node.name, node.pagerank
ORDER BY score DESC LIMIT 5
"""


@dataclass(frozen=True)
class IngestStats:
    """Graph totals after ingest (identical on re-run — see idempotency)."""

    files: int
    functions: int
    call_edges: int


def _iter_named_nodes(root, wanted: str):
    """Yield every `wanted` node in the tree (leaves-ward traversal)."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == wanted:
            yield node
            continue  # a function's nested defs are their own roots
        stack.extend(reversed(node.children))


def _calls_in(function_node) -> list[str]:
    """Simple-name identifier calls anywhere inside a function body.

    Attribute calls (`obj.method()`) are ignored by design: their field
    `function` is an `attribute` node, not an `identifier`.
    """
    names: list[str] = []
    stack = list(function_node.children)
    while stack:
        node = stack.pop()
        if node.type == "call":
            callee = node.child_by_field_name("function")
            if callee is not None and callee.type == "identifier":
                names.append(callee.text.decode("utf8"))
        stack.extend(node.children)
    return names


def ingest(gateway: GraphGateway, src_path: str | None = None) -> IngestStats:
    """Parse every .py file under src_path into the graph, then rank it."""
    root = Path(src_path or config.SRC_PATH)
    py_files = sorted(root.rglob("*.py"))
    pending_edges: list[tuple[str, str]] = []

    # Pass 1: all File/Function/DEFINES nodes + collect CALLS pairs.
    for path in py_files:
        rel = path.relative_to(root).as_posix()
        source = path.read_bytes()
        tree = PARSER.parse(source)

        gateway.query(
            "MERGE (f:File {filepath: $fp}) SET f.extension = $ext",
            {"fp": rel, "ext": ".py"},
        )

        for func in _iter_named_nodes(tree.root_node, "function_definition"):
            name_node = func.child_by_field_name("name")
            if name_node is None:
                continue
            fname = name_node.text.decode("utf8")
            gateway.query("MERGE (f:Function {name: $name})", {"name": fname})
            gateway.query(
                """
                MATCH (file:File {filepath: $fp}), (fn:Function {name: $name})
                MERGE (file)-[:DEFINES]->(fn)
                """,
                {"fp": rel, "name": fname},
            )
            for callee in _calls_in(func):
                pending_edges.append((fname, callee))

    # Pass 2: CALLS edges — every endpoint now exists regardless of file order.
    for caller, callee in pending_edges:
        gateway.query(
            """
            MATCH (a:Function {name: $a}), (b:Function {name: $b})
            WHERE NOT (a)-[:CALLS]->(b)
            CREATE (a)-[:CALLS]->(b)
            """,
            {"a": caller, "b": callee},
        )

    # Calibration round: pagerank written onto nodes (pinned query verbatim).
    gateway.query(PAGERANK_WRITE)

    files = gateway.query("MATCH (f:File) RETURN count(f)", read_only=True).result_set[0][0]
    functions = gateway.query("MATCH (n:Function) RETURN count(n)", read_only=True).result_set[0][0]
    edges = gateway.query("MATCH ()-[r:CALLS]->() RETURN count(r)", read_only=True).result_set[0][0]
    return IngestStats(files=int(files), functions=int(functions), call_edges=int(edges))


if __name__ == "__main__":
    gw = GraphGateway.open()
    try:
        stats = ingest(gw)
        print(f"ingested: {stats.files} files, {stats.functions} functions, {stats.call_edges} call edges")
    finally:
        gw.close()