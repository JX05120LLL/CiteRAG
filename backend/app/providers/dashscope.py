"""Bounded DashScope HTTP adapter for CiteRAG's selected Beijing models."""

from __future__ import annotations

import base64
import json
import math
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.credentials import WORKSPACE_PATTERN, DashScopeConfig
from app.providers.errors import ProviderDiagnostic, ProviderError
from app.providers.types import (
    Completion,
    EmbeddingBatch,
    Message,
    ProviderUsage,
    RequestBudget,
    RerankBatch,
    RerankItem,
)

PROVIDER = "dashscope"
VISION_MODEL = "qwen3.8-omni-flash"
EMBEDDING_SINGLE_TEXT_TOKEN_LIMIT = 8192
TIMEOUT = httpx.Timeout(connect=5, read=45, write=10, pool=5)
LIMITS = httpx.Limits(max_connections=5, max_keepalive_connections=2)


class DashScopeClient:
    def __init__(
        self,
        config: DashScopeConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        budget: RequestBudget | None = None,
    ) -> None:
        if config.region != "cn-beijing" or not WORKSPACE_PATTERN.fullmatch(
            config.workspace_id
        ):
            raise ValueError("invalid DashScope Beijing workspace configuration")
        self._config = config
        self._budget = budget
        self._client = httpx.AsyncClient(
            base_url=f"https://{config.workspace_id}.cn-beijing.maas.aliyuncs.com",
            headers={"Authorization": f"Bearer {config.api_key.get_secret_value()}"},
            timeout=TIMEOUT,
            limits=LIMITS,
            transport=transport,
        )

    @property
    def config(self) -> DashScopeConfig:
        return self._config

    async def __aenter__(self) -> DashScopeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, Any], model: str) -> httpx.Response:
        if self._budget is not None:
            await self._budget.reserve()
        try:
            response = await self._client.post(path, json=payload)
        except httpx.TimeoutException:
            raise ProviderError("timeout", PROVIDER, model) from None
        except httpx.RequestError:
            raise ProviderError("network", PROVIDER, model) from None
        if response.is_success:
            return response
        request_id = _request_id(response)
        category, diagnostic = _error_category(
            response, embedding_model=model == self._config.models["embedding"]
        )
        raise ProviderError(
            category,
            PROVIDER,
            model,
            status=response.status_code,
            request_id=request_id,
            diagnostic=diagnostic,
        )

    async def complete(
        self, model: str, messages: list[Message], *, max_tokens: int,
        response_format: str | None = None,
    ) -> Completion:
        if model not in {
            self._config.models["answer"],
            self._config.models["engine"],
            self._config.models["summary"],
        }:
            raise ValueError("completion model is not configured")
        if not messages or any(not message.content for message in messages):
            raise ValueError("completion messages must not be empty")
        output_limit = 2048 if model == self._config.models["answer"] else 512
        if max_tokens < 1 or max_tokens > output_limit:
            raise ValueError(f"completion max_tokens must be between 1 and {output_limit}")
        if response_format not in {None, "json_object"}:
            raise ValueError("unsupported response format")
        response = await self._post(
            "/compatible-mode/v1/chat/completions",
            {
                "model": model,
                "messages": [
                    {"role": message.role, "content": message.content} for message in messages
                ],
                "max_tokens": max_tokens,
                **({"response_format": {"type": "json_object"}}
                   if response_format == "json_object" else {}),
                **({"temperature": 0} if model == self._config.models["answer"] else {}),
            },
            model,
        )
        body = _json_object(response, model)
        try:
            choices = body["choices"]
            choice = choices[0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choice, dict)
                or not isinstance(content, str)
                or not content
                or (finish_reason is not None and not isinstance(finish_reason, str))
            ):
                raise TypeError
        except (KeyError, IndexError, TypeError):
            raise _protocol_error(model, response) from None
        if model == self._config.models["answer"] and finish_reason == "length":
            raise ProviderError("output_limit", PROVIDER, model, status=response.status_code)
        return Completion(
            model=model,
            content=content,
            finish_reason=finish_reason,
            request_id=_request_id(response, body),
            usage=_usage(body.get("usage"), model, response),
        )

    async def observe_images(self, images: list[tuple[str, bytes]]) -> Completion:
        """Extract bounded visual observations; never expose a private file URL."""
        if not 1 <= len(images) <= 2 or any(
            mime not in {"image/png", "image/jpeg"} or not data or len(data) > 10 * 1024 * 1024
            for mime, data in images
        ):
            raise ValueError("vision input must contain one or two bounded PNG/JPEG images")
        content = [{"type": "text", "text": (
            "只观察图片中可直接看见的内容和文字，不推断外部资料。"
            "返回 JSON 对象：observation 为最多 250 字的中文描述，优先保留"
            "可见的编号、单位、对象和限定条件；"
            "uncertain_identifiers 为看不清或有多个候选的编号数组，最多 5 个。"
            "若看不清，明确写出不确定，不猜测编号。"
        )}]
        content.extend({"type": "image_url", "image_url": {
            "url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        }} for mime, data in images)
        response = await self._post("/compatible-mode/v1/chat/completions", {
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": content}],
            "modalities": ["text"],
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
            "max_tokens": 1200,
        }, VISION_MODEL)
        body = _json_object(response, VISION_MODEL)
        try:
            choices = body["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            content = choice["message"]["content"]
            reason = choice.get("finish_reason")
            if reason == "length":
                raise ProviderError("output_limit", PROVIDER, VISION_MODEL,
                                    status=response.status_code)
            if not isinstance(content, str) or not content or reason != "stop":
                raise ValueError
        except (ValueError, KeyError, TypeError, IndexError):
            raise _protocol_error(VISION_MODEL, response) from None
        return Completion(VISION_MODEL, content, reason, _request_id(response, body),
                          _usage(body.get("usage"), VISION_MODEL, response))

    async def stream_complete(
        self, model: str, messages: list[Message], *, max_tokens: int
    ) -> AsyncIterator[str]:
        if model != self._config.models["answer"]:
            raise ValueError("streaming is limited to the answer model")
        if not messages or any(not message.content for message in messages):
            raise ValueError("completion messages must not be empty")
        if max_tokens < 1 or max_tokens > 2048:
            raise ValueError("completion max_tokens must be between 1 and 2048")
        if self._budget is not None:
            await self._budget.reserve()
        try:
            async with self._client.stream("POST", "/compatible-mode/v1/chat/completions", json={
                "model": model,
                "messages": [{"role": item.role, "content": item.content} for item in messages],
                "max_tokens": max_tokens, "stream": True, "temperature": 0,
            }) as response:
                def protocol_error() -> ProviderError:
                    # A streaming response is not buffered for body-based diagnostics.
                    return ProviderError("protocol", PROVIDER, model, status=response.status_code)

                if not response.is_success:
                    await response.aread()
                    category, diagnostic = _error_category(response, embedding_model=False)
                    raise ProviderError(category, PROVIDER, model, status=response.status_code,
                                        request_id=_request_id(response), diagnostic=diagnostic)
                if not response.headers.get("content-type", "").startswith("text/event-stream"):
                    raise protocol_error()
                finished = done = False
                total = 0
                async for line in response.aiter_lines():
                    total += len(line)
                    if total > 32768:
                        raise protocol_error()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data: "):
                        raise protocol_error()
                    data = line[6:]
                    if data == "[DONE]":
                        done = True
                        break
                    try:
                        payload = json.loads(data)
                        choices = payload["choices"]
                        if not isinstance(choices, list) or len(choices) > 1:
                            raise ValueError
                        if not choices:
                            continue  # Optional usage-only trailer.
                        choice = choices[0]
                        if choice["index"] != 0 or not isinstance(choice["delta"], dict):
                            raise ValueError
                        content = choice["delta"].get("content")
                        reason = choice.get("finish_reason")
                        if content is not None and not isinstance(content, str):
                            raise ValueError
                        if finished and (content or reason is not None):
                            raise ValueError
                        if content:
                            yield content
                        if reason is not None:
                            if reason == "length":
                                raise ProviderError("output_limit", PROVIDER, model,
                                                    status=response.status_code)
                            if reason != "stop" or finished:
                                raise ValueError
                            finished = True
                    except (ValueError, TypeError, KeyError, IndexError):
                        raise protocol_error() from None
                if not finished or not done:
                    raise protocol_error()
        except httpx.TimeoutException:
            raise ProviderError("timeout", PROVIDER, model) from None
        except httpx.RequestError:
            raise ProviderError("network", PROVIDER, model) from None

    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        model = self._config.models["embedding"]
        if not texts or any(not isinstance(text, str) or not text for text in texts):
            raise ValueError("embedding input must contain non-empty strings")
        if len(texts) > 10:
            raise ValueError("embedding batch cannot exceed 10 inputs")
        response = await self._post(
            "/compatible-mode/v1/embeddings",
            {
                "model": model,
                "input": texts,
                "dimensions": self._config.embedding_dimension,
                "encoding_format": "float",
            },
            model,
        )
        body = _json_object(response, model)
        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise _protocol_error(model, response)
        indexed: dict[int, tuple[float, ...]] = {}
        for item in data:
            if not isinstance(item, dict):
                raise _protocol_error(model, response)
            index = item.get("index")
            vector = item.get("embedding")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(texts)
                or index in indexed
                or not isinstance(vector, list)
                or len(vector) != self._config.embedding_dimension
                or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                    for value in vector
                )
            ):
                raise _protocol_error(model, response)
            indexed[index] = tuple(float(value) for value in vector)
        if set(indexed) != set(range(len(texts))):
            raise _protocol_error(model, response)
        return EmbeddingBatch(
            model=model,
            vectors=tuple(indexed[index] for index in range(len(texts))),
            request_id=_request_id(response, body, allow_response_id=True),
            usage=_usage(body.get("usage"), model, response),
        )

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int
    ) -> RerankBatch:
        model = self._config.models["rerank"]
        if not isinstance(query, str) or not query:
            raise ValueError("rerank query must not be empty")
        if not documents or any(not isinstance(item, str) or not item for item in documents):
            raise ValueError("rerank documents must contain non-empty strings")
        if len(documents) > 100:
            raise ValueError("qwen3-vl-rerank supports at most 100 text documents")
        if top_n < 1 or top_n > len(documents):
            raise ValueError("rerank top_n must be within the document count")
        response = await self._post(
            "/api/v1/services/rerank/text-rerank/text-rerank",
            {
                "model": model,
                "input": {
                    "query": {"text": query},
                    "documents": [{"text": document} for document in documents],
                },
                "parameters": {"top_n": top_n},
            },
            model,
        )
        body = _json_object(response, model)
        output = body.get("output")
        if not isinstance(output, dict):
            raise _protocol_error(model, response)
        results = output.get("results")
        if not isinstance(results, list) or not results or len(results) > top_n:
            raise _protocol_error(model, response)
        items: list[RerankItem] = []
        indices: set[int] = set()
        for result in results:
            if not isinstance(result, dict):
                raise _protocol_error(model, response)
            index = result.get("index")
            score = result.get("relevance_score")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(documents)
                or index in indices
                or not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(score)
            ):
                raise _protocol_error(model, response)
            indices.add(index)
            items.append(RerankItem(index=index, relevance_score=float(score)))
        items.sort(key=lambda item: item.relevance_score, reverse=True)
        return RerankBatch(
            model=model,
            items=tuple(items),
            request_id=_request_id(response, body),
            usage=_usage(body.get("usage"), model, response),
        )


def _request_id(
    response: httpx.Response,
    body: dict[str, Any] | None = None,
    *,
    allow_response_id: bool = False,
) -> str | None:
    header = response.headers.get("x-request-id") or response.headers.get(
        "x-dashscope-request-id"
    )
    if header:
        return header[:200]
    if body is None:
        try:
            decoded = response.json()
        except ValueError:
            decoded = None
        body = decoded if isinstance(decoded, dict) else None
    if body is not None:
        for candidate in (body, body.get("error")):
            if not isinstance(candidate, dict):
                continue
            value = candidate.get("request_id")
            if isinstance(value, str) and value:
                return value[:200]
        # Only the successful Embedding endpoint uses body `id` here as a
        # request identifier. Chat completion `id` identifies the object.
        if allow_response_id:
            value = body.get("id")
            if isinstance(value, str) and value:
                return value[:200]
    return None


def _error_diagnostic(response: httpx.Response) -> tuple[ProviderDiagnostic, str]:
    try:
        body = response.json()
    except ValueError:
        body = None
        shape = "non_json"
    else:
        if isinstance(body, dict):
            if "error" not in body:
                shape = "top_level"
                details = body
            elif isinstance(body["error"], dict):
                shape = "error_object"
                details = body["error"]
            else:
                shape = "error_other"
                details = {}
        else:
            shape = "json_other"
    if not isinstance(body, dict):
        details = {}

    raw_code = details.get("code")
    if not isinstance(raw_code, str) or not raw_code:
        raw_code = details.get("type")
    code = raw_code.strip().lower() if isinstance(raw_code, str) else ""
    if not code:
        code_class = "missing"
    elif code in {"internalerror.algo.invalidparameter", "invalidparameter"}:
        code_class = "invalid_parameter"
    elif code in {"invalid_value", "invalid_request_error"}:
        code_class = "compatible_parameter"
    else:
        code_class = "other"

    raw_message = details.get("message")
    message = raw_message.strip().lower()[:1000] if isinstance(raw_message, str) else ""
    input_range = None
    match = re.match(
        r"^range of input length should be\s*\[\s*(\d{1,5})\s*,\s*(\d{1,5})\s*\]",
        message,
    )
    if match:
        message_class = "input_range"
        input_range = tuple(map(int, match.groups()))
    elif not message:
        message_class = "missing"
    elif "dimension" in message:
        message_class = "dimensions"
    elif "batch" in message:
        message_class = "batch_size"
    elif ("input" in message or "text" in message) and any(
        term in message for term in ("length", "token", "too long", "exceed", "limit")
    ):
        message_class = "input_length_generic"
    elif "input" in message and any(term in message for term in ("format", "type")):
        message_class = "input_format"
    else:
        message_class = "other"
    return ProviderDiagnostic(shape, code_class, message_class, input_range), code


def _error_category(
    response: httpx.Response, *, embedding_model: bool
) -> tuple[str, ProviderDiagnostic]:
    diagnostic, code = _error_diagnostic(response)
    if response.status_code == 401:
        return "authentication", diagnostic
    if response.status_code == 403:
        return "permission", diagnostic
    if response.status_code == 429:
        return "rate_limit", diagnostic
    if code in {"invalid_api_key", "invalidapikey"}:
        return "authentication", diagnostic
    if any(marker in code for marker in ("arrearage", "quota", "balance", "insufficient")):
        return "quota", diagnostic
    if (
        embedding_model
        and response.status_code == 400
        and diagnostic.code_class == "invalid_parameter"
        and diagnostic.input_range is not None
    ):
        if diagnostic.input_range == (1, EMBEDDING_SINGLE_TEXT_TOKEN_LIMIT):
            return "input_limit", diagnostic
        return "input_limit_other", diagnostic
    if response.status_code >= 500:
        return "upstream", diagnostic
    return "request", diagnostic


def _json_object(response: httpx.Response, model: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise _protocol_error(model, response) from None
    if not isinstance(body, dict):
        raise _protocol_error(model, response)
    return body


def _protocol_error(model: str, response: httpx.Response) -> ProviderError:
    return ProviderError(
        "protocol", PROVIDER, model, status=response.status_code, request_id=_request_id(response)
    )


def _usage(value: Any, model: str, response: httpx.Response) -> ProviderUsage:
    if value is None:
        return ProviderUsage()
    if not isinstance(value, dict):
        raise _protocol_error(model, response)
    fields: dict[str, int | None] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        item = value.get(name)
        if item is not None and (
            not isinstance(item, int) or isinstance(item, bool) or item < 0
        ):
            raise _protocol_error(model, response)
        fields[name] = item
    return ProviderUsage(**fields)
