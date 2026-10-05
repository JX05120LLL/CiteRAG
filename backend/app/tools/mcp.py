"""Reviewed Streamable HTTP MCP adapter. Discovery never registers tools automatically."""

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from app.services.errors import ServiceError
from app.tools.contracts import arguments_fingerprint, normalize_mcp_result
from app.tools.gateway import ToolDefinition


class _SafeMCPDiagnostics(logging.Filter):
    """The SDK's validation traceback can contain entire remote responses."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = "MCP transport diagnostic; check the persisted tool outcome."
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


# This API owns its MCP client. Keep severity/module/time, never remote content,
# session identifiers, request parameters or exception traces in SDK diagnostics.
for _logger_name in ("mcp.client.streamable_http", "mcp.shared.session"):
    logging.getLogger(_logger_name).addFilter(_SafeMCPDiagnostics())


@dataclass(frozen=True)
class ReviewedMCPTool:
    id: str
    title: str
    scope: str
    descriptor: dict
    approval_required: bool = False

    def __post_init__(self):
        if not isinstance(self.approval_required, bool):
            raise ValueError("MCP approval_required must be a boolean")


def _connection_failure(error: BaseException, status: int | None = None) -> ServiceError:
    """Keep upstream HTTP diagnostics and URLs out of business errors."""
    import httpx2

    if isinstance(error, httpx2.TimeoutException):
        return ServiceError(503, "mcp_timeout", "MCP 服务超时，请先核对调用记录")
    if isinstance(error, httpx2.HTTPStatusError):
        status = error.response.status_code
    if status is not None:
        code, message = {
            401: ("mcp_auth_failed", "MCP 认证失败；当前适配器尚未接入凭证加载"),
            403: ("mcp_access_denied", "MCP 服务拒绝访问，请检查服务权限"),
            429: ("mcp_rate_limited", "MCP 服务限流，请稍后主动重试"),
        }.get(status,
              ("mcp_unavailable", "MCP 服务暂不可用，请检查连接与服务状态"))
        return ServiceError(503, code, message)
    return ServiceError(503, "mcp_unavailable", "MCP 服务暂不可用，请检查连接与服务状态")


class MCPAdapter:
    def __init__(self, url: str, server_version: str):
        parsed = urlsplit(url)
        try:
            local = parsed.hostname == "localhost" or ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = False
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.scheme == "http"
            and not local
            or not server_version
            or any(character.isspace() for character in url)
            or "\\" in url
        ):
            raise ValueError("MCP endpoint must be a fixed reviewed HTTPS or loopback HTTP URL")
        self.url, self.server_version = url, server_version
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def connection(self):
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        upstream_status = None

        async def capture_status(response):
            nonlocal upstream_status
            # The SDK converts non-2xx POSTs into generic JSON-RPC errors. Preserve
            # only the status, never a body/header/URL, for safe business diagnostics.
            if response.request.method == "POST" and response.status_code >= 400:
                upstream_status = response.status_code

        async def only_registered_endpoint(request):
            if str(request.url).rstrip("/") != self.url.rstrip("/"):
                raise ServiceError(403, "mcp_redirect_forbidden", "MCP 不能转发到未登记目标")

        try:
            async with (
                self.lock,
                httpx2.AsyncClient(
                    timeout=15,
                    follow_redirects=False,
                    trust_env=False,
                    event_hooks={"request": [only_registered_endpoint],
                                 "response": [capture_status]},
                ) as http,
            ):
                async with streamable_http_client(self.url, http_client=http) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        initialized = await session.initialize()
                        if initialized.server_info.version != self.server_version:
                            raise ServiceError(
                                409, "mcp_version_changed", "MCP 版本变化，须重新审查"
                            )
                        yield session
        except BaseExceptionGroup as error:
            pending = list(error.exceptions)
            failures = []
            while pending:
                item = pending.pop()
                if isinstance(item, BaseExceptionGroup):
                    pending.extend(item.exceptions)
                elif isinstance(item, asyncio.CancelledError):
                    raise asyncio.CancelledError from None
                elif isinstance(item, ServiceError):
                    raise item from None
                else:
                    failures.append(item)
            raise _connection_failure(failures[0] if failures else error, upstream_status) from None
        except httpx2.HTTPError as error:
            raise _connection_failure(error, upstream_status) from None

    async def _descriptors(self, session):
        page = await session.list_tools()
        if page.next_cursor is not None or len(page.tools) > 50:
            raise ServiceError(409, "mcp_catalog_exceeded", "MCP 目录超过首版审查范围")
        # These are candidates only, never model-visible metadata by discovery alone.
        return [
            item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in page.tools
        ]

    async def discover(self) -> list[dict]:
        async with self.connection() as session:
            return await self._descriptors(session)

    async def call(self, tool: ReviewedMCPTool, arguments: dict) -> dict:
        arguments_fingerprint(arguments)
        schema = {**tool.descriptor["inputSchema"], "additionalProperties": False}
        if list(Draft202012Validator(schema).iter_errors(arguments)):
            raise ServiceError(422, "tool_arguments_invalid", "MCP 参数不符合已审查格式")
        async with self.connection() as session:
            descriptors = await self._descriptors(session)
            remote = next(
                (item for item in descriptors if item["name"] == tool.descriptor["name"]), None
            )
            if remote != tool.descriptor:
                raise ServiceError(409, "mcp_contract_changed", "MCP 工具契约变化，须重新审查")
            result = await session.call_tool(
                tool.descriptor["name"], arguments, read_timeout_seconds=15
            )
            result = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            if "content" not in result and "structuredContent" not in result:
                raise ServiceError(503, "mcp_result_unsupported", "MCP 返回能力尚未接入")
            output_schema = tool.descriptor.get("outputSchema")
            if not result.get("isError") and output_schema is not None:
                if "structuredContent" not in result or list(
                    Draft202012Validator(output_schema).iter_errors(result["structuredContent"])
                ):
                    raise ServiceError(503, "mcp_output_invalid", "MCP 结果不符合已审查格式")
            normalized = normalize_mcp_result(result)
            if normalized["status"] != "succeeded":
                raise ServiceError(503, normalized["error_code"], "MCP 工具未成功，请核对服务状态")
            return normalized

    def definition(self, tool: ReviewedMCPTool) -> ToolDefinition:
        schema = {**tool.descriptor["inputSchema"], "additionalProperties": False}

        def reject_remote_refs(value):
            if isinstance(value, dict):
                for key in ("$ref", "$dynamicRef"):
                    if key in value and not str(value[key]).startswith("#"):
                        raise ValueError("MCP schemas cannot fetch external references")
                for item in value.values():
                    reject_remote_refs(item)
            elif isinstance(value, list):
                for item in value:
                    reject_remote_refs(item)

        reject_remote_refs(schema)
        Draft202012Validator.check_schema(schema)
        output_schema = tool.descriptor.get("outputSchema")
        if output_schema is not None:
            reject_remote_refs(output_schema)
            Draft202012Validator.check_schema(output_schema)

        validator = Draft202012Validator(schema)

        def validate(arguments):
            try:
                arguments_fingerprint(arguments)
                return None if next(validator.iter_errors(arguments), None) else arguments
            except (ValueError, TypeError):
                return None

        async def run(_session, _context, arguments):
            result = await self.call(tool, arguments)
            return {**result["data"], "truncated": result["truncated"]}

        definition = ToolDefinition(
            tool.id,
            tool.title,
            tool.scope,
            tool.approval_required,
            "读取已审查 MCP 服务；参数会发往所登记服务，结果不是知识库引用。",
            validate,
            run,
            version=self.server_version,
            input_schema=schema,
            backend="mcp",
            destination=self.url,
        )
        # Manual approvals persist the tool version. Bind it to the reviewed
        # destination and descriptor so a registry edit invalidates old approval.
        reviewed = hashlib.sha256(json.dumps(
            {"policy": definition.policy_hash, "descriptor": tool.descriptor,
             "title": tool.title},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode()).hexdigest()
        return replace(definition, version=f"mcp-{reviewed}")


def load_reviewed_registry(path: Path) -> dict[str, ToolDefinition]:
    """Maintainer file only: no public URL/command registration or inline credentials."""
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("Unsafe or oversized MCP registry")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) > 8:
        raise ValueError("MCP registry must contain at most eight reviewed services")
    result = {}
    for server in data:
        if (
            set(server) != {"url", "version", "tools"}
            or not isinstance(server["tools"], list)
            or len(server["tools"]) > 8
        ):
            raise ValueError("MCP service fields must be explicitly reviewed")
        adapter = MCPAdapter(server["url"], server["version"])
        for item in server["tools"]:
            tool = ReviewedMCPTool(**item)
            if not tool.id.startswith("mcp.") or tool.id in result:
                raise ValueError("MCP IDs must be unique in their separate namespace")
            result[tool.id] = adapter.definition(tool)
    return result
