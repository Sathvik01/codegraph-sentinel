"""Ingestion tests: schema shape, call edges, PageRank calibration."""

from codegraph_sentinel import config
from codegraph_sentinel.ingest import ingest

PAGERANK_QUERY = """
CALL algo.pageRank('Function', 'CALLS')
YIELD node, score
RETURN node.name, score
ORDER BY score DESC
"""


def _count(gw, cypher):
    return gw.query(cypher, read_only=True).result_set[0][0]


def test_ingest_counts(gw_ingested):
    stats = ingest(gw_ingested)
    assert stats.files == 5
    assert stats.functions == 7
    assert stats.call_edges == 5


def test_defines_edges(gw_ingested):
    rows = gw_ingested.query(
        "MATCH (f:File {filepath: 'core.py'})-[:DEFINES]->(fn {name: 'validate_identity'}) RETURN count(fn)",
        read_only=True,
    ).result_set
    assert rows[0][0] == 1


def test_calls_edges_created(gw_ingested):
    one = "MATCH (a {name: $a})-[:CALLS]->(b {name: $b}) RETURN count(*)"
    assert gw_ingested.query(one, {"a": "parse_config", "b": "validate_identity"}, read_only=True).result_set[0][0] == 1
    assert gw_ingested.query(one, {"a": "run_job", "b": "load_tenant"}, read_only=True).result_set[0][0] == 1
    assert gw_ingested.query(one, {"a": "run_job", "b": "checksum"}, read_only=True).result_set[0][0] == 1


def test_multihop_chain_exists(gw_ingested):
    rows = gw_ingested.query(
        "MATCH (a {name: 'run_job'})-[:CALLS]->(b {name: 'load_tenant'})-[:CALLS]->(c {name: 'validate_identity'}) RETURN count(*)",
        read_only=True,
    ).result_set
    assert rows[0][0] == 1


def test_pagerank_hub_is_highest(gw_ingested):
    rows = gw_ingested.query(PAGERANK_QUERY, read_only=True).result_set
    top_name, top_score = rows[0]
    assert top_name == "validate_identity"
    # Calibration note (plan Review Focus #3): if this absolute assertion fails
    # due to score normalization, weight the mock repo's CALLS edges or adjust
    # PAGERANK_BLOCK_THRESHOLD — never the gate logic.
    assert top_score > config.PAGERANK_BLOCK_THRESHOLD, (
        f"hub score {top_score} must clear the block threshold "
        f"{config.PAGERANK_BLOCK_THRESHOLD}"
    )


def test_leaf_isolated(gw_ingested):
    rows = gw_ingested.query(
        "MATCH (f {name: 'format_timestamp'}) WHERE NOT (f)-[:CALLS]->() AND NOT ()-[:CALLS]->(f) RETURN count(f)",
        read_only=True,
    ).result_set
    assert rows[0][0] == 1


def test_ingest_idempotent(gw_ingested):
    first = ingest(gw_ingested)
    second = ingest(gw_ingested)
    assert second == first
    dupes = _count(
        gw_ingested,
        "MATCH (a:Function)-[c:CALLS]->(b:Function) WITH a, b, count(c) AS n WHERE n > 1 RETURN count(n)",
    )
    assert dupes == 0