"""Shared fixtures: gateway on the dev FalkorDB server + ingested graph."""

import pytest

from codegraph_sentinel.graph_gateway import GraphGateway


@pytest.fixture
def gw():
    """A fresh gateway (singleton guard satisfied: closed on teardown)."""
    g = GraphGateway.open()
    yield g
    g.close()


@pytest.fixture
def gw_ingested(gw):
    """Gateway whose default graph was wiped and re-ingested from mock_repo/."""
    from codegraph_sentinel.ingest import ingest

    gw.graph().query("MATCH (n) DETACH DELETE n")
    ingest(gw)
    return gw


@pytest.fixture
def mcp_app(gw_ingested):
    """The module-level FastMCP app wired to an ingested gateway (plan B4)."""
    from codegraph_sentinel.mcp_server import mcp, set_gateway

    set_gateway(gw_ingested)
    yield mcp
    set_gateway(None)