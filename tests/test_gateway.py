"""Gateway contract: liveness, roundtrip, reopen persistence, singleton guard."""

import pytest

from codegraph_sentinel.graph_gateway import GraphGateway, GraphUnavailableError


def test_ping_true_on_fresh_server():
    gw = GraphGateway.open()
    try:
        assert gw.ping() is True
    finally:
        gw.close()


def test_write_read_roundtrip():
    gw = GraphGateway.open()
    try:
        gw.graph().query("CREATE (:Person {name: $n})", params={"n": "Alice"})
        rows = gw.query("MATCH (p:Person) RETURN p.name", read_only=True)
        assert rows.result_set == [["Alice"]]
    finally:
        gw.graph().query("MATCH (n:Person) DETACH DELETE n")
        gw.close()


def test_data_persists_across_handles():
    gw = GraphGateway.open()
    gw.graph().query("CREATE (:Person {name: 'Bob'})")
    gw.close()
    gw2 = GraphGateway.open()
    try:
        assert gw2.query("MATCH (n:Person) RETURN n.name").result_set == [["Bob"]]
    finally:
        gw2.graph().query("MATCH (n:Person) DETACH DELETE n")
        gw2.close()


def test_second_instance_same_connection_rejected():
    gw = GraphGateway.open()
    try:
        with pytest.raises(RuntimeError):
            GraphGateway.open()
    finally:
        gw.close()


def test_connect_failure_raises_unavailable():
    # falkordb connects eagerly: an unreachable endpoint surfaces as
    # GraphUnavailableError from open() itself, never a bare transport error.
    gw = GraphGateway.open()
    gw.close()
    with pytest.raises(GraphUnavailableError):
        GraphGateway.open(host="127.0.0.1", port=1)  # nothing listens here


def test_query_after_close_raises_unavailable():
    gw = GraphGateway.open()
    gw.close()
    with pytest.raises(GraphUnavailableError):
        gw.query("RETURN 1", read_only=True)