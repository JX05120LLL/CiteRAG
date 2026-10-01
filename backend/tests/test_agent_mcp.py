"""Actual MCP Streamable HTTP handshake against a public synthetic local server."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager

import pytest


@asynccontextmanager
async def synthetic_server():
    import uvicorn
    from mcp.server import MCPServer

    server = MCPServer("CiteRAG synthetic", version="synthetic-1", log_level="ERROR")

    @server.tool()
    def add(a: int, b: int) -> dict:
        """Add two public synthetic numbers."""
        return {"sum": a + b}

    @server.tool()
    def broken() -> dict:
        """Synthetic failure; never expose diagnostics."""
        raise ValueError("secret synthetic diagnostic")

    app = server.streamable_http_app(stateless_http=True, json_response=True)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    http = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", access_log=False)
    )
    task = asyncio.create_task(http.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if http.started:
                break
            await asyncio.sleep(0.01)
        assert http.started
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        http.should_exit = True
        await task
        listener.close()


async def test_real_protocol_result_and_reviewed_contract_changes():
    from app.tools.mcp import MCPAdapter, ReviewedMCPTool

    async with synthetic_server() as url:
        adapter = MCPAdapter(url, "synthetic-1")
        descriptors = await adapter.discover()
        add = next(item for item in descriptors if item["name"] == "add")
        tool = ReviewedMCPTool("mcp.synthetic.add", "合成加法", "any", add)
        result = await adapter.call(tool, {"a": 2, "b": 3})
        assert json.loads(result["data"]["text"]) == {"sum": 5}
        changed = ReviewedMCPTool(
            "mcp.synthetic.add", "合成加法", "any", {**add, "description": "modified"}
        )
        from app.services.errors import ServiceError

        with pytest.raises(ServiceError, match="MCP"):
            await adapter.call(changed, {"a": 2, "b": 3})


async def test_mcp_tool_error_is_not_success_and_no_unreviewed_tools():
    from app.services.errors import ServiceError
    from app.tools.mcp import MCPAdapter, ReviewedMCPTool

    async with synthetic_server() as url:
        adapter = MCPAdapter(url, "synthetic-1")
        descriptors = await adapter.discover()
        broken = next(item for item in descriptors if item["name"] == "broken")
        with pytest.raises(ServiceError) as error:
            await adapter.call(
                ReviewedMCPTool("mcp.synthetic.broken", "合成失败", "any", broken), {}
            )
        assert error.value.code == "mcp_tool_failed"
        assert "secret" not in str(error.value)


def test_mcp_endpoint_must_be_a_reviewed_fixed_transport():
    from app.tools.mcp import MCPAdapter

    for url in (
        "file:///private",
        "http://external.example/mcp",
        "https://user:key@host/mcp",
        "https://host/mcp?token=private",
    ):
        with pytest.raises(ValueError):
            MCPAdapter(url, "reviewed")


@pytest.mark.parametrize("key", ["$ref", "$dynamicRef"])
def test_reviewed_output_schema_cannot_fetch_remote_documents(key):
    from app.tools.mcp import MCPAdapter, ReviewedMCPTool

    descriptor = {
        "name": "synthetic",
        "inputSchema": {"type": "object"},
        "outputSchema": {key: "https://unreviewed.example/schema"},
    }
    with pytest.raises(ValueError, match="external references"):
        MCPAdapter("http://127.0.0.1:1/mcp", "reviewed").definition(
            ReviewedMCPTool("mcp.synthetic", "合成工具", "any", descriptor)
        )
