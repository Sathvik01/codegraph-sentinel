"""MCP tool layer: discovery, pinned payload shapes, DB_UNAVAILABLE mapping."""

import json

from fastmcp import Client

from codegraph_sentinel.mcp_tools_adapter import load_mcp_tools

FIVE_TOOLS = {
    "list_functions",
    "get_centrality",
    "get_blast_radius",
    "get_memory_rules",
    "record_architectural_rule",
}


def _payload(result) -> dict:
    """Absorb fastmcp's str-vs-dict variation (plan B4 step 4)."""
    data = result.data
    if isinstance(data, str):
        data = json.loads(data)
    assert isinstance(data, dict), f"unexpected tool payload type: {type(data)}"
    return data


async def test_tool_list(mcp_app):
    async with Client(mcp_app) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == FIVE_TOOLS


async def test_get_centrality_payload(mcp_app):
    async with Client(mcp_app) as client:
        payload = _payload(
            await client.call_tool("get_centrality", {"target_func": "run_job"})
        )
    assert payload["status"] == "ok"
    assert payload["target_func"] == "run_job"
    assert isinstance(payload["pagerank"], float)


async def test_get_centrality_unknown_function(mcp_app):
    async with Client(mcp_app) as client:
        payload = _payload(
            await client.call_tool("get_centrality", {"target_func": "nope_fn"})
        )
    assert payload["status"] == "unknown_function"


async def test_list_functions_payload(mcp_app):
    async with Client(mcp_app) as client:
        payload = _payload(await client.call_tool("list_functions", {}))
    assert payload["status"] == "ok"
    assert "validate_identity" in payload["functions"]


async def test_blast_radius_payload_shape(mcp_app):
    async with Client(mcp_app) as client:
        payload = _payload(
            await client.call_tool("get_blast_radius", {"target_func": "validate_identity"})
        )
    assert payload["status"] == "ok"
    assert payload["total_dependencies"] >= 3
    assert isinstance(payload["chains"], list) and payload["chains"]


async def test_db_unavailable_returns_payload_not_exception(mcp_app, gw_ingested):
    gw_ingested.close()  # server "dies" mid-conversation (gateway handle gone)
    async with Client(mcp_app) as client:
        result = await client.call_tool(
            "get_blast_radius", {"target_func": "run_job"}
        )
    payload = _payload(result)
    assert payload == {"status": "DB_UNAVAILABLE"}  # payload, not an exception


async def test_record_rule_roundtrip(mcp_app):
    async with Client(mcp_app) as client:
        recorded = _payload(
            await client.call_tool(
                "record_architectural_rule",
                {"target_func": "format_timestamp", "rule": "Leaf node; safe to refactor."},
            )
        )
        rules = _payload(
            await client.call_tool(
                "get_memory_rules", {"target_func": "format_timestamp"}
            )
        )
    assert recorded["status"] == "ok" and recorded["recorded"] is True
    assert recorded["timestamp"]
    assert len(rules["rules"]) == 1
    assert "Leaf node" in rules["rules"][0]["rule"]


async def test_adapter_lists_and_invokes(mcp_app):
    tools, session = await load_mcp_tools(mcp_app)
    assert {t.name for t in tools} == FIVE_TOOLS
    async with session:
        centrality = next(t for t in tools if t.name == "get_centrality")
        out = await centrality.ainvoke({"target_func": "run_job"})
        assert isinstance(out, str)
        payload = json.loads(out)
        assert payload["status"] == "ok" and isinstance(payload["pagerank"], float)
        listing = next(t for t in tools if t.name == "list_functions")
        empty = await listing.ainvoke({})
        assert json.loads(empty)["status"] == "ok"