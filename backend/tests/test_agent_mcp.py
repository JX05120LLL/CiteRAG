"""Actual MCP Streamable HTTP handshake against a public synthetic local server."""

import asyncio
import hashlib
import json
import socket
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest


@asynccontextmanager
async def synthetic_server(server=None):
    import uvicorn
    from mcp.server import MCPServer

    if server is None:
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


def test_mcp_definition_rejects_bad_arguments_before_execution():
    from app.tools.mcp import MCPAdapter, ReviewedMCPTool

    descriptor = {"name": "add", "inputSchema": {
        "type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"],
    }}
    spec = MCPAdapter("http://127.0.0.1:1/mcp", "reviewed").definition(
        ReviewedMCPTool("mcp.synthetic.add", "合成加法", "any", descriptor)
    )
    assert spec.validate({"a": 2}) == {"a": 2}
    for arguments in ({}, {"a": "2"}, {"a": True}, {"a": 2, "secret": "private"}):
        assert spec.validate(arguments) is None


def test_mcp_reviewed_version_changes_with_target_or_contract_not_legacy_false():
    from app.tools.mcp import MCPAdapter, ReviewedMCPTool

    descriptor = {"name": "add", "inputSchema": {"type": "object"}}
    tool = ReviewedMCPTool("mcp.synthetic.add", "合成加法", "any", descriptor, True)
    original = MCPAdapter("http://127.0.0.1:17651/mcp", "reviewed").definition(tool)
    changed_target = MCPAdapter("http://127.0.0.1:17652/mcp", "reviewed").definition(tool)
    changed_contract = MCPAdapter("http://127.0.0.1:17651/mcp", "reviewed").definition(
        ReviewedMCPTool(tool.id, tool.title, tool.scope,
                        {**descriptor, "description": "updated"}, True))
    changed_approval = MCPAdapter("http://127.0.0.1:17651/mcp", "reviewed").definition(
        ReviewedMCPTool(tool.id, tool.title, tool.scope, descriptor, False))
    assert len({original.version, changed_target.version, changed_contract.version}) == 3
    assert changed_approval.version == original.version


@pytest.mark.parametrize("approval", [None, False, True])
def test_mcp_registry_without_review_requires_approval(tmp_path, approval):
    from app.tools.mcp import load_reviewed_registry

    item = {"id": "mcp.synthetic.add", "title": "合成加法", "scope": "any",
            "descriptor": {"name": "add", "inputSchema": {"type": "object"}}}
    if approval is not None:
        item["approval_required"] = approval
    path = tmp_path / "registry.json"
    path.write_text(json.dumps([{"url": "http://127.0.0.1:1/mcp", "version": "reviewed",
                                 "tools": [item]}]), encoding="utf-8")
    spec = load_reviewed_registry(path)[item["id"]]
    assert spec.approval_required is True
    assert spec.unattended_read_review is None


def _public_add_review(url, version, descriptor):
    return {
        "reviewed_by": "maintainer", "reviewed_at": "2026-10-06T08:00:00Z",
        "evidence_ref": "docs/reviews/synthetic-add.md", "source": "controlled synthetic server",
        "deployment_id": "synthetic-release-1", "behavior": "Add two public integers only",
        "data_destination": url, "allowed_data": "public",
        "allowed_arguments_schema": {"type": "object", "properties": {
            "a": {"type": "integer", "minimum": 0, "maximum": 10},
            "b": {"type": "integer", "minimum": 0, "maximum": 10},
        }, "required": ["a", "b"], "additionalProperties": False},
        "permissions": [], "cost": "free", "url": url, "server_version": version,
        "descriptor_sha256": hashlib.sha256(json.dumps(
            descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode()).hexdigest(),
    }


def _write_review_registry(path, *, review=None, approval=False, descriptor=None):
    descriptor = descriptor or {"name": "add", "inputSchema": {"type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"]}}
    url, version = "http://127.0.0.1:17651/mcp", "controlled-1"
    item = {"id": "mcp.synthetic.add", "title": "Synthetic add", "scope": "any",
            "approval_required": approval, "descriptor": descriptor}
    if review is not None:
        item["unattended_read_review"] = review
    path.write_text(json.dumps([{"url": url, "version": version, "tools": [item]}]),
                    encoding="utf-8")
    return url, version, descriptor


def test_mcp_valid_public_review_is_bound_to_fixed_registry(tmp_path):
    from app.tools.mcp import load_reviewed_registry

    path = tmp_path / "registry.json"
    url, version, descriptor = _write_review_registry(path)
    review = _public_add_review(url, version, descriptor)
    _write_review_registry(path, review=review)
    spec = load_reviewed_registry(path)["mcp.synthetic.add"]
    assert spec.approval_required is True  # Catalog remains conservative.
    assert spec.unattended_read_review == review
    assert spec.version != load_reviewed_registry(
        _write_unreviewed_registry(tmp_path / "old-version.json")
    )["mcp.synthetic.add"].version
    assert spec.policy_hash != load_reviewed_registry(
        _write_unreviewed_registry(tmp_path / "old.json")
    )["mcp.synthetic.add"].policy_hash


def test_mcp_explicit_approval_remains_required_with_valid_review(tmp_path):
    from app.tools.mcp import load_reviewed_registry

    path = tmp_path / "registry.json"
    url, version, descriptor = _write_review_registry(path)
    review = _public_add_review(url, version, descriptor)
    _write_review_registry(path, review=review, approval=True)
    spec = load_reviewed_registry(path)["mcp.synthetic.add"]
    assert spec.approval_required is True
    assert spec.unattended_read_review is None


def _write_unreviewed_registry(path):
    _write_review_registry(path)
    return path


@pytest.mark.parametrize("change", [
    lambda review: review.pop("reviewed_by"),
    lambda review: review.update(extra="not reviewed"),
    lambda review: review.update(url="http://127.0.0.1:17652/mcp"),
    lambda review: review.update(server_version="changed"),
    lambda review: review.update(descriptor_sha256="0" * 64),
    lambda review: review.update(permissions=["read_private"]),
    lambda review: review.update(cost="metered"),
    lambda review: review.update(allowed_data="private"),
    lambda review: review.update(data_destination="https://elsewhere.example"),
    lambda review: review.update(allowed_arguments_schema={"type": "object",
        "properties": {"text": {"type": "string"}}, "additionalProperties": False}),
    lambda review: review.update(allowed_arguments_schema={"type": "object",
        "properties": {"nested": {"type": "object"}}, "additionalProperties": False}),
    lambda review: review.update(allowed_arguments_schema={"type": "object",
        "properties": {"a": {"type": "number", "minimum": 0}},
        "additionalProperties": False}),
    lambda review: review["allowed_arguments_schema"].update(required=[[]]),
    lambda review: review["allowed_arguments_schema"]["properties"]["a"].update(
        minimum=float("nan")),
])
def test_mcp_invalid_public_review_fails_registration(tmp_path, change):
    from app.tools.mcp import load_reviewed_registry

    path = tmp_path / "registry.json"
    url, version, descriptor = _write_review_registry(path)
    review = _public_add_review(url, version, descriptor)
    change(review)
    _write_review_registry(path, review=review)
    with pytest.raises(ValueError, match="review"):
        load_reviewed_registry(path)


@pytest.mark.parametrize("approval", ["false", 0, {}, None])
def test_mcp_registry_rejects_non_boolean_approval(tmp_path, approval):
    from app.tools.mcp import load_reviewed_registry

    path = tmp_path / "registry.json"
    path.write_text(json.dumps([{"url": "http://127.0.0.1:1/mcp", "version": "reviewed",
        "tools": [{"id": "mcp.synthetic", "title": "合成", "scope": "any",
                   "approval_required": approval,
                   "descriptor": {"name": "synthetic", "inputSchema": {"type": "object"}}}]
    }]), encoding="utf-8")
    with pytest.raises(ValueError, match="approval"):
        load_reviewed_registry(path)


@pytest.mark.parametrize("status, code", [(401, "mcp_auth_failed"),
    (403, "mcp_access_denied"), (429, "mcp_rate_limited"), (503, "mcp_unavailable")])
async def test_mcp_http_errors_have_safe_specific_reasons(monkeypatch, status, code):
    import httpx2

    from app.services.errors import ServiceError
    from app.tools.mcp import MCPAdapter

    original = httpx2.AsyncClient
    transport = httpx2.MockTransport(lambda request: httpx2.Response(
        status, json={"error": "private provider diagnostic"}, request=request))
    monkeypatch.setattr(httpx2, "AsyncClient",
                        lambda **kwargs: original(transport=transport, **kwargs))
    adapter = MCPAdapter("http://127.0.0.1:1/mcp", "reviewed")
    with pytest.raises(ServiceError) as failure:
        async with adapter.connection():
            pytest.fail("Failed MCP handshake must not yield a session")
    assert failure.value.code == code
    assert "private" not in str(failure.value)


async def test_local_reference_mcp_service_uses_same_bounded_calculator():
    from app.tools.local_mcp import create_server, reviewed_registry
    from app.tools.mcp import MCPAdapter, ReviewedMCPTool

    server = create_server()
    async with synthetic_server(server) as url:
        entry = (await reviewed_registry(server, url))[0]
        spec = entry["tools"][0]
        assert spec["approval_required"] is True
        adapter = MCPAdapter(url, entry["version"])
        tool = ReviewedMCPTool(**spec)
        result = await adapter.call(tool, {"expression": "0.1 + 0.2"})
        assert json.loads(result["data"]["text"])["value"] == "0.3"
        from app.services.errors import ServiceError

        with pytest.raises(ServiceError) as failure:
            await adapter.call(tool, {"expression": "1 / 0"})
        assert failure.value.code == "mcp_tool_failed"


def test_local_reference_cli_prints_utf8_registry_without_binding(tmp_path):
    from app.tools.mcp import load_reviewed_registry

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        # An occupied port must not prevent offline printing or replace the listener.
        output = subprocess.run(
            [sys.executable, "-m", "app.tools.local_mcp", "--port", str(port), "--print-registry"],
            capture_output=True, check=True, timeout=10,
        ).stdout.decode("utf-8")
        path = tmp_path / "registry.json"
        path.write_text(output, encoding="utf-8")
        spec = load_reviewed_registry(path)["mcp.local.calculate"]
        assert spec.title == "本地 MCP 计算器"
        assert spec.approval_required is True
        assert spec.destination == f"http://127.0.0.1:{port}/mcp"


async def test_malformed_mcp_response_does_not_log_provider_content(monkeypatch, caplog):
    import logging

    import httpx2

    from app.services.errors import ServiceError
    from app.tools.mcp import MCPAdapter

    diagnostic = "private provider diagnostic"
    original = httpx2.AsyncClient
    transport = httpx2.MockTransport(lambda request: httpx2.Response(
        200, content=diagnostic.encode(), headers={"Content-Type": "application/json"},
        request=request))
    monkeypatch.setattr(httpx2, "AsyncClient",
                        lambda **kwargs: original(transport=transport, **kwargs))
    caplog.set_level(logging.DEBUG, logger="mcp.client.streamable_http")
    with pytest.raises(ServiceError):
        async with MCPAdapter("http://127.0.0.1:1/mcp", "reviewed").connection():
            pytest.fail("Malformed MCP response must not initialize")
    assert diagnostic not in caplog.text
    assert any(record.levelno >= logging.ERROR for record in caplog.records)
