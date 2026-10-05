"""Explicitly started loopback-only read-only MCP reference service; no database or keys."""

import argparse
import asyncio
import json
import socket
import sys

from app.tools.calculator import calculate as decimal_calculate

VERSION = "citerag-local-tools-1"


def create_server():
    from mcp.server import MCPServer

    server = MCPServer("CiteRAG local tools", version=VERSION, log_level="ERROR")

    @server.tool()
    async def calculate(expression: str) -> dict:
        """Local decimal + - * / and parentheses, 28 significant digits; rounded means approximate.

        No code execution, files, network or knowledge access. The expression is bounded.
        """
        return await decimal_calculate(None, None, {"expression": expression})

    return server


async def reviewed_registry(server, url: str) -> list[dict]:
    """Print this example's own descriptors offline, not discover or trust another server."""
    tools = await server.list_tools()
    return [{"url": url, "version": VERSION, "tools": [
        {"id": "mcp.local.calculate", "title": "本地 MCP 计算器", "scope": "any",
         "approval_required": True,
         "descriptor": tool.model_dump(mode="json", by_alias=True, exclude_none=True)}
        for tool in tools
    ]}]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--print-registry", action="store_true",
                        help="Print a review template without starting or contacting a service")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    server = create_server()
    if args.print_registry:
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(asyncio.run(reviewed_registry(server, f"http://127.0.0.1:{args.port}/mcp")),
                         ensure_ascii=False, indent=2))
        return
    import uvicorn

    # Bind the socket ourselves: fail if occupied, never replace another listener.
    with socket.socket() as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", args.port))
        listener.listen()
        app = server.streamable_http_app(stateless_http=True, json_response=True,
                                        max_request_body_size=8192)
        uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                     log_level="warning", access_log=False)).run(sockets=[listener])


if __name__ == "__main__":
    main()
