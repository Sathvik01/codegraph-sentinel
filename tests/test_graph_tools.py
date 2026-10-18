"""Policy graph functions: blast radius, centrality, memory rules, ambiguity."""

import pytest

from codegraph_sentinel.graph_tools import (
    AmbiguousTargetException,
    get_blast_radius,
    get_function_names,
    get_function_pagerank,
    get_memory_rules,
    get_top_centrality,
    record_memory_rule,
)


def test_function_names_sorted_and_complete(gw_ingested):
    names = get_function_names(gw_ingested)
    assert names == sorted(names)
    assert {"validate_identity", "run_job", "format_timestamp"} <= set(names)


def test_top_centrality_hub_first(gw_ingested):
    top = get_top_centrality(gw_ingested, n=3)
    assert top[0][0] == "validate_identity"
    assert len(top) == 3
    assert all(isinstance(score, float) for _, score in top)


def test_pagerank_target(gw_ingested):
    assert get_function_pagerank(gw_ingested, "validate_identity") is not None


def test_missing_function(gw_ingested):
    assert get_function_pagerank(gw_ingested, "nope_fn") is None


def test_blast_radius_includes_direct_callers(gw_ingested):
    br = get_blast_radius(gw_ingested, "validate_identity")
    assert {"parse_config", "load_tenant", "compliance_report"} <= {
        c["caller"] for c in br["chains"]
    }


def test_blast_radius_multihop_chain(gw_ingested):
    chains = get_blast_radius(gw_ingested, "validate_identity")["chains"]
    run_job_chains = [c for c in chains if c["caller"] == "run_job"]
    assert run_job_chains and all(c["hops"] >= 2 for c in run_job_chains)
    # headline proof chain is order-exact
    assert ["run_job", "load_tenant", "validate_identity"] in [
        c["chain"] for c in run_job_chains
    ]


def test_record_and_read_memory_rule(gw_ingested):
    recorded = record_memory_rule(gw_ingested, "format_timestamp", "Leaf node; verified safe to refactor.")
    assert recorded["rule"] == "Leaf node; verified safe to refactor."
    assert recorded["timestamp"]
    rules = get_memory_rules(gw_ingested, "format_timestamp")
    assert len(rules) == 1 and "Leaf node" in rules[0]["rule"]
    assert rules[0]["timestamp"] == recorded["timestamp"]


def test_rule_persists_across_close(gw_ingested):
    record_memory_rule(gw_ingested, "checksum", "Stable helper; covered by run_job.")
    gw_ingested.close()
    from codegraph_sentinel.graph_gateway import GraphGateway

    gw2 = GraphGateway.open()
    try:
        rules = get_memory_rules(gw2, "checksum")
        assert len(rules) == 1
        assert "Stable helper" in rules[0]["rule"]
    finally:
        gw2.close()


def test_ambiguous_target(gw_ingested):
    gw_ingested.graph().query(
        "CREATE (:Function {name: 'validate_identity', pagerank: 0})"
    )
    with pytest.raises(AmbiguousTargetException):
        get_blast_radius(gw_ingested, "validate_identity")
    with pytest.raises(AmbiguousTargetException):
        get_function_pagerank(gw_ingested, "validate_identity")