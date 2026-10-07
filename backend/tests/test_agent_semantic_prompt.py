"""Offline planning prompt regression; no model or supplier calls."""

from types import SimpleNamespace


async def test_agent_prompt_uses_supplied_parameters_and_available_catalog(monkeypatch):
    from app.rag.runtime import RagRuntime

    captured = {}

    class FakeClient:
        async def complete(self, _model, messages, **_kwargs):
            captured["system"] = messages[0].content
            captured["user"] = messages[-1].content
            return SimpleNamespace(content='{"action":"finish","answer":{"text":"ok"}}')

    async def fake_client(_self):
        return FakeClient()

    monkeypatch.setattr(RagRuntime, "_get_client", fake_client)
    runtime = object.__new__(RagRuntime)
    await runtime.complete_agent(
        "What is the weather in Hanzhong today?", [],
        {"task_supplements": [{"reply_text": "Hanzhong City, Hantai District"}]},
        [], [], True,
    )
    assert "Hanzhong" in captured["user"]
    assert "task_supplements" in captured["user"]
    assert "已经提供" in captured["system"]
    assert "没有匹配工具" in captured["system"]
