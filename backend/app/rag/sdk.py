"""Lazy SDK construction, deliberately unavailable until providers are supplied."""

import json
import os
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

from app.rag.engine import assert_isolated_configuration

LIGHTRAG_COMMIT = '59af311307c7417b342f44850b097648d47e83bd'


def verify_sdk_revision() -> None:
    try:
        package = distribution('lightrag-hku')
    except PackageNotFoundError:
        raise RuntimeError('Install the locked rag extra before configuring the engine') from None
    provenance = json.loads(package.read_text('direct_url.json') or '{}')
    if provenance.get('vcs_info', {}).get('commit_id') != LIGHTRAG_COMMIT:
        raise RuntimeError('LightRAG must be installed from the project locked source commit')


def sdk_factory(
    *,
    llm_model_func: Callable,
    embedding_func: Any,
    rerank_model_func: Callable,
) -> Callable:
    """Injected model adapters must be explicitly configured; no implicit provider fallback.

    Creating the factory does not initialize PG or contact a model. EngineManager owns
    initialization. Model dimensions and provider/rerank behavior require real M0 probes.
    """
    assert_isolated_configuration(os.environ, Path.cwd())
    verify_sdk_revision()
    if not all(callable(value) for value in (llm_model_func, embedding_func, rerank_model_func)):
        raise ValueError('LLM, embedding and rerank adapters are required')
    # Upstream calls load_dotenv at import time. Only explicitly supplied process
    # configuration is allowed, never implicit loading of legacy .env files.
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    from lightrag import LightRAG

    def create(workspace: str, working_dir: Path):
        assert_isolated_configuration(os.environ, Path.cwd())
        return LightRAG(
            workspace=workspace,
            working_dir=str(working_dir),
            kv_storage='PGKVStorage',
            vector_storage='PGVectorStorage',
            graph_storage='PGTableGraphStorage',
            doc_status_storage='PGDocStatusStorage',
            chunk_token_size=1200,
            chunk_overlap_token_size=100,
            llm_model_func=llm_model_func,
            embedding_func=embedding_func,
            rerank_model_func=rerank_model_func,
        )

    return create
