"""Synthetic Omni request contract; no supplier connection or private credential."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.credentials import DashScopeConfig
from app.providers.dashscope import DashScopeClient
from app.providers.errors import ProviderError


@pytest.mark.asyncio
async def test_omni_image_observation_uses_private_data_uri_and_text_only():
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": (
                '{"observation":"红色标签","uncertain_identifiers":[]}'
            )}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        })

    config = DashScopeConfig(
        region="cn-beijing", workspace_id="synthetic-workspace",
        models={"answer": "qwen-flash", "engine": "qwen-plus", "summary": "qwen-max",
                "embedding": "text-embedding-v4", "rerank": "qwen3-vl-rerank"},
        embedding_dimension=1024, api_key=SecretStr("synthetic-test-key"),
        credential_ciphertext_sha256="0" * 64,
    )
    async with DashScopeClient(config, transport=httpx.MockTransport(handler)) as client:
        result = await client.observe_images([("image/png", b"synthetic PNG bytes")])
    assert result.model == "qwen3.8-omni-flash"
    assert json.loads(result.content)["observation"] == "红色标签"
    assert requests[0]["model"] == "qwen3.8-omni-flash"
    assert requests[0]["modalities"] == ["text"]
    assert requests[0]["reasoning_effort"] == "none"
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert requests[0]["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert "synthetic-test-key" not in json.dumps(requests)


@pytest.mark.asyncio
async def test_omni_truncated_output_has_distinct_failure_reason():
    async def handler(_request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
        })

    config = DashScopeConfig(
        region="cn-beijing", workspace_id="synthetic-workspace",
        models={"answer": "qwen-flash", "engine": "qwen-plus", "summary": "qwen-max",
                "embedding": "text-embedding-v4", "rerank": "qwen3-vl-rerank"},
        embedding_dimension=1024, api_key=SecretStr("synthetic-test-key"),
        credential_ciphertext_sha256="0" * 64,
    )
    async with DashScopeClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as failure:
            await client.observe_images([("image/png", b"synthetic PNG bytes")])
    assert failure.value.category == "output_limit"
