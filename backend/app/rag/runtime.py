"""Production LightRAG adapter assembly; no provider request occurs at construction."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, Settings
from app.credentials import load_dashscope_config
from app.providers.dashscope import DashScopeClient
from app.providers.types import Message, ProviderUsage, RequestBudget
from app.rag.database import (
    RagDatabaseError,
    RagDatabaseProbe,
    RagDatabaseSettings,
    load_rag_database_settings,
    probe_rag_database,
)
from app.rag.engine import Engine, EngineManager, assert_isolated_configuration
from app.rag.sdk import sdk_factory, verify_sdk_revision

POSTGRES_ENV_KEYS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DATABASE",
)
MODEL_CREDENTIAL_RECORD = PROJECT_ROOT / ".local/runtime/models/dashscope.credential.xml"


def build_sdk_factory(
    client: DashScopeClient,
    database_settings: RagDatabaseSettings,
    *,
    on_provider_result: Callable[[str, str | None, ProviderUsage], None] | None = None,
    max_output_tokens: int = 512,
    max_provider_input_chars: int | None = None,
) -> Callable[[str, Path], Any]:
    """Map the selected provider contract to this pinned LightRAG revision."""

    if (
        database_settings.host != "127.0.0.1"
        or database_settings.port != 55433
        or database_settings.database != "assistant_rag"
        or client.config.models["engine"] != "qwen-plus"
        or client.config.embedding_dimension != 1024
    ):
        raise ValueError("incompatible local LightRAG runtime configuration")
    if not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= 512:
        raise ValueError("RAG LLM output limit must be between 1 and 512")
    if max_provider_input_chars is not None and (
        not isinstance(max_provider_input_chars, int)
        or isinstance(max_provider_input_chars, bool)
        or not 1 <= max_provider_input_chars <= 20_000
    ):
        raise ValueError("RAG provider input limit must be between 1 and 20000")

    verify_sdk_revision()
    import numpy as np
    from lightrag.utils import EmbeddingFunc

    async def llm_for_lightrag(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> str:
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("LightRAG prompt must be non-empty")
        messages: list[Message] = []
        if system_prompt:
            messages.append(Message(role="system", content=system_prompt))
        for prior in history_messages or []:
            role = prior.get("role")
            content = prior.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("LightRAG history message is invalid")
            messages.append(Message(role=role, content=content))
        messages.append(Message(role="user", content=prompt))
        if max_provider_input_chars is not None and (
            sum(len(message.content) for message in messages) > max_provider_input_chars
        ):
            raise ValueError("RAG provider input limit exceeded")
        requested_tokens = kwargs.get("max_tokens", 512)
        if not isinstance(requested_tokens, int) or isinstance(requested_tokens, bool):
            raise ValueError("LightRAG max_tokens must be an integer")
        completion = await client.complete(
            "qwen-plus", messages, max_tokens=min(requested_tokens, max_output_tokens)
        )
        if on_provider_result is not None:
            on_provider_result(completion.model, completion.request_id, completion.usage)
        return completion.content

    async def embed_for_lightrag(
        texts: list[str], *, embedding_dim: int = 1024, **_kwargs: Any
    ) -> np.ndarray:
        if embedding_dim != 1024:
            raise ValueError("LightRAG embedding dimension must be 1024")
        if max_provider_input_chars is not None and (
            sum(len(text) for text in texts) > max_provider_input_chars
        ):
            raise ValueError("RAG provider input limit exceeded")
        batch = await client.embed(texts)
        if on_provider_result is not None:
            on_provider_result(batch.model, batch.request_id, batch.usage)
        return np.asarray(batch.vectors, dtype=np.float32)

    async def rerank_for_lightrag(
        query: str,
        documents: list[str],
        top_n: int | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, float | int]]:
        count = len(documents) if top_n is None else min(top_n, len(documents))
        if max_provider_input_chars is not None and (
            len(query) + sum(len(document) for document in documents)
            > max_provider_input_chars
        ):
            raise ValueError("RAG provider input limit exceeded")
        batch = await client.rerank(query, documents, top_n=count)
        if on_provider_result is not None:
            on_provider_result(batch.model, batch.request_id, batch.usage)
        return [
            {"index": item.index, "relevance_score": item.relevance_score}
            for item in batch.items
        ]

    embedding_func = EmbeddingFunc(
        embedding_dim=1024,
        max_token_size=8192,
        model_name="text-embedding-v4",
        send_dimensions=True,
        func=embed_for_lightrag,
    )
    return sdk_factory(
        llm_model_name="qwen-plus",
        llm_model_func=llm_for_lightrag,
        embedding_func=embedding_func,
        rerank_model_func=rerank_for_lightrag,
    )


class RagRuntime:
    """Own the optional engine database probe, provider client, and SDK instances."""

    def __init__(
        self,
        settings: Settings,
        *,
        request_budget: RequestBudget | None = None,
        on_provider_result: Callable[[str, str | None, ProviderUsage], None] | None = None,
        max_output_tokens: int = 512,
        max_provider_input_chars: int | None = None,
    ) -> None:
        if not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= 512:
            raise ValueError("RAG LLM output limit must be between 1 and 512")
        if max_provider_input_chars is not None and (
            not isinstance(max_provider_input_chars, int)
            or isinstance(max_provider_input_chars, bool)
            or not 1 <= max_provider_input_chars <= 20_000
        ):
            raise ValueError("RAG provider input limit must be between 1 and 20000")
        self.settings = settings
        self._request_budget = request_budget
        self._on_provider_result = on_provider_result
        self._max_output_tokens = max_output_tokens
        self._max_provider_input_chars = max_provider_input_chars
        self.probe: RagDatabaseProbe | None = None
        self.probe_error: str | None = None
        self.database_settings: RagDatabaseSettings | None = None
        self._owner_assertion: Callable[[], None] | None = None
        self._manager: EngineManager | None = None
        self._client: DashScopeClient | None = None
        self._prior_environment: dict[str, str | None] | None = None
        self._lock = asyncio.Lock()
        self._started = False
        self._closed = False

    async def start(self, owner_assertion: Callable[[], None]) -> RagRuntime:
        if self._closed:
            raise RuntimeError("RAG runtime is closed")
        if self._started:
            return self
        owner_assertion()
        assert_isolated_configuration(os.environ, Path.cwd())
        self._owner_assertion = owner_assertion
        self._started = True
        if not self.settings.rag_database_record.is_file():
            return self
        try:
            database_settings = load_rag_database_settings(self.settings.rag_database_record)
            self.probe = await probe_rag_database(database_settings)
            self.database_settings = database_settings
        except RagDatabaseError as error:
            self.probe_error = str(error)
        return self

    async def get(self, kb_id: Any) -> Engine:
        if self._closed:
            raise RuntimeError("RAG runtime is closed")
        if not self._started or self._owner_assertion is None:
            raise RuntimeError("RAG runtime has not started")
        self._owner_assertion()
        if self.probe is None or self.database_settings is None:
            raise RagDatabaseError("engine database is unavailable")
        async with self._lock:
            self._owner_assertion()
            if self._closed:
                raise RuntimeError("RAG runtime is closed")
            if self._manager is None:
                config = load_dashscope_config(MODEL_CREDENTIAL_RECORD)
                if self._request_budget is None:
                    client = DashScopeClient(config)
                else:
                    client = DashScopeClient(config, budget=self._request_budget)
                try:
                    self._apply_database_environment(self.database_settings)
                    options: dict[str, Any] = {}
                    if self._on_provider_result is not None:
                        options["on_provider_result"] = self._on_provider_result
                    if self._max_output_tokens != 512:
                        options["max_output_tokens"] = self._max_output_tokens
                    if self._max_provider_input_chars is not None:
                        options["max_provider_input_chars"] = self._max_provider_input_chars
                    factory = build_sdk_factory(client, self.database_settings, **options)
                    self._manager = EngineManager(
                        self.settings.rag_workspace_root, factory, self._owner_assertion
                    )
                    self._client = client
                except BaseException:
                    self._restore_database_environment()
                    await client.aclose()
                    raise
            manager = self._manager
        return await manager.get(kb_id)

    def _apply_database_environment(self, database: RagDatabaseSettings) -> None:
        if self._prior_environment is not None:
            raise RuntimeError("RAG PostgreSQL environment is already active")
        values = {
            "POSTGRES_HOST": database.host,
            "POSTGRES_PORT": str(database.port),
            "POSTGRES_USER": database.username,
            "POSTGRES_PASSWORD": database.password.get_secret_value(),
            "POSTGRES_DATABASE": database.database,
        }
        prior = {name: os.environ.get(name) for name in POSTGRES_ENV_KEYS}
        if any(prior[name] not in {None, values[name]} for name in POSTGRES_ENV_KEYS):
            raise RuntimeError("conflicting RAG PostgreSQL environment")
        self._prior_environment = prior
        os.environ.update(values)

    def _restore_database_environment(self) -> None:
        if self._prior_environment is None:
            return
        for name, prior in self._prior_environment.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior
        self._prior_environment = None

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            manager, self._manager = self._manager, None
            client, self._client = self._client, None
        errors: list[Exception] = []
        try:
            if manager is not None:
                try:
                    await manager.close()
                except Exception as error:
                    errors.append(error)
            if client is not None:
                try:
                    await client.aclose()
                except Exception as error:
                    errors.append(error)
        finally:
            self._restore_database_environment()
        if errors:
            raise ExceptionGroup("RAG runtime cleanup failed", errors)
