"""In-memory FastMCP client -> LangChain tools.

`load_mcp_tools` opens a `fastmcp.Client` over the module's server, performs
real MCP tool discovery via `list_tools()`, and wraps each entry as a
`langchain_core.tools.StructuredTool` whose coroutine forwards to
`client.call_tool(name, args)`. The returned async context manager keeps the
client session open for the graph's lifetime — the agent can only reach the
graph through these five discovered tools.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastmcp import Client, FastMCP
from langchain_core.tools import StructuredTool
from pydantic import create_model

_JSON_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _args_model(tool: Any) -> type:
    """Build a pydantic args model from the discovered JSON input schema."""
    schema = tool.input_schema or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for name, spec in properties.items():
        annotation = _JSON_TYPES.get((spec or {}).get("type", ""), Any)
        default = ... if name in required else None
        fields[name] = (annotation, default)
    return create_model(f"{tool.name}_Args", **fields)


def _as_json_str(result: Any) -> str:
    """Normalize a CallToolResult to the JSON string the policy expects."""
    data = getattr(result, "data", None)
    if isinstance(data, str):
        return data
    if data is None:
        texts = [
            getattr(block, "text", "")
            for block in (getattr(result, "content", None) or [])
        ]
        return json.dumps({"status": "raw", "text": "".join(t for t in texts if t)})
    return json.dumps(data)


def _make_tool(client: Client, tool: Any) -> StructuredTool:
    async def coroutine(**kwargs: Any) -> str:
        result = await client.call_tool(tool.name, kwargs)
        return _as_json_str(result)

    return StructuredTool(
        name=tool.name,
        description=tool.description or "",
        coroutine=coroutine,
        args_schema=_args_model(tool),
    )


async def load_mcp_tools(mcp: FastMCP):
    """Discover tools over the in-memory MCP transport.

    Returns `(tools, session)`: enter `session` (async context manager) to
    keep the client open for the agent's lifetime.
    """
    client = Client(mcp)
    await client.__aenter__()
    try:
        discovered = await client.list_tools()
        tools = [_make_tool(client, tool) for tool in discovered]
    except BaseException as exc:
        await client.__aexit__(type(exc), exc, exc.__traceback__)
        raise

    @asynccontextmanager
    async def session() -> AsyncIterator[list[StructuredTool]]:
        try:
            yield tools
        finally:
            await client.__aexit__(None, None, None)

    return tools, session()