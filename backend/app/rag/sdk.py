"""Lazy SDK construction, deliberately unavailable until providers are supplied."""

import hashlib
import json
import os
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.rag.engine import assert_isolated_configuration

LIGHTRAG_COMMIT = '59af311307c7417b342f44850b097648d47e83bd'
TOKENIZER_SHA256 = '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'
TOKENIZER_SOURCE = PROJECT_ROOT / '.local/runtime/tokenizer/o200k_base.tiktoken'
_TOKENIZER_URL = 'https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'


def chunking_by_source_span(
    tokenizer: Any,
    content: str,
    split_by_character: str | None = None,
    split_by_character_only: bool = False,
    chunk_overlap_token_size: int = 100,
    chunk_token_size: int = 1200,
) -> list[dict[str, Any]]:
    """Pinned SDK callback: slice original characters, never decode token windows.

    Byte-level BPE windows can split a Chinese character and introduce U+FFFD.
    The SDK's safe span API verifies token budgets on complete source substrings.
    Keep the legacy six-argument callback contract used by RAW ingestion.
    """
    if chunk_token_size <= 0 or not 0 <= chunk_overlap_token_size < chunk_token_size:
        raise ValueError('Invalid chunk token size or overlap')
    chunks: list[dict[str, Any]] = []
    segments = content.split(split_by_character) if split_by_character else [content]
    for segment in segments:
        if split_by_character and split_by_character_only:
            from lightrag.exceptions import ChunkTokenLimitExceededError

            token_count = len(tokenizer.encode(segment))
            if token_count > chunk_token_size:
                # No private preview is attached to errors crossing this boundary.
                raise ChunkTokenLimitExceededError(token_count, chunk_token_size)
            parts = [segment]
        else:
            spans = tokenizer.split_by_token_limit(
                segment, chunk_token_size, chunk_overlap_token_size,
            )
            parts = [segment[span.start:span.end] for span in spans]
        for part in parts:
            # Preserve the verified substring, including boundary whitespace:
            # stripping can change BPE merges and exceed the checked token budget.
            if part.strip():
                chunks.append({
                    'content': part,
                    'tokens': len(tokenizer.encode(part)),
                    'chunk_order_index': len(chunks),
                })
    return chunks


def verify_sdk_revision() -> None:
    try:
        package = distribution('lightrag-hku')
    except PackageNotFoundError:
        raise RuntimeError('Install the locked rag extra before configuring the engine') from None
    provenance = json.loads(package.read_text('direct_url.json') or '{}')
    if provenance.get('vcs_info', {}).get('commit_id') != LIGHTRAG_COMMIT:
        raise RuntimeError('LightRAG must be installed from the project locked source commit')


def _verified_tokenizer() -> Any:
    """Load the hash-pinned local encoding without an implicit network download."""
    try:
        source = TOKENIZER_SOURCE.read_bytes()
    except OSError:
        raise RuntimeError('Pinned tokenizer asset is missing from local runtime') from None
    if hashlib.sha256(source).hexdigest() != TOKENIZER_SHA256:
        raise RuntimeError('Pinned tokenizer asset hash does not match')
    cache = TOKENIZER_SOURCE.parent / hashlib.sha1(_TOKENIZER_URL.encode()).hexdigest()
    if cache.exists():
        try:
            cached = cache.read_bytes()
        except OSError:
            raise RuntimeError('Pinned tokenizer cache is unavailable') from None
        if hashlib.sha256(cached).hexdigest() != TOKENIZER_SHA256:
            raise RuntimeError('Pinned tokenizer cache hash does not match')
    else:
        temporary = cache.with_name(cache.name + f'.{os.urandom(8).hex()}.tmp')
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, cache)
        except OSError:
            raise RuntimeError('Pinned tokenizer cache could not be prepared') from None
        finally:
            temporary.unlink(missing_ok=True)

    previous = os.environ.get('TIKTOKEN_CACHE_DIR')
    os.environ['TIKTOKEN_CACHE_DIR'] = str(TOKENIZER_SOURCE.parent)
    try:
        from lightrag.utils import TiktokenTokenizer

        return TiktokenTokenizer('gpt-4o-mini')
    finally:
        if previous is None:
            os.environ.pop('TIKTOKEN_CACHE_DIR', None)
        else:
            os.environ['TIKTOKEN_CACHE_DIR'] = previous


def sdk_factory(
    *,
    llm_model_func: Callable,
    embedding_func: Any,
    rerank_model_func: Callable,
    llm_model_name: str = 'qwen-plus',
) -> Callable:
    """Injected model adapters must be explicitly configured; no implicit provider fallback.

    Creating the factory does not initialize PG or contact a model. EngineManager owns
    initialization. Model dimensions and provider/rerank behavior require real M0 probes.
    """
    assert_isolated_configuration(os.environ, Path.cwd())
    verify_sdk_revision()
    if not all(callable(value) for value in (llm_model_func, embedding_func, rerank_model_func)):
        raise ValueError('LLM, embedding and rerank adapters are required')
    if llm_model_name != 'qwen-plus':
        raise ValueError('The M0 engine model must be qwen-plus')
    # Upstream calls load_dotenv at import time. Only explicitly supplied process
    # configuration is allowed, never implicit loading of legacy .env files.
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    from lightrag import LightRAG

    def create(workspace: str, working_dir: Path):
        assert_isolated_configuration(os.environ, Path.cwd())
        tokenizer = _verified_tokenizer()
        return LightRAG(
            workspace=workspace,
            working_dir=str(working_dir),
            kv_storage='PGKVStorage',
            vector_storage='PGVectorStorage',
            graph_storage='PGTableGraphStorage',
            doc_status_storage='PGDocStatusStorage',
            chunk_token_size=1200,
            chunk_overlap_token_size=100,
            chunking_func=chunking_by_source_span,
            tokenizer=tokenizer,
            llm_model_name=llm_model_name,
            llm_model_func=llm_model_func,
            embedding_func=embedding_func,
            rerank_model_func=rerank_model_func,
        )

    return create
