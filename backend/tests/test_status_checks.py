"""Free local system checks never invoke a model or speech supplier."""

import asyncio
from datetime import datetime

from test_postgres_local import environment  # noqa: F401

pytest_plugins = ["test_postgres_local"]


async def test_local_check_reports_reason_and_time_without_supplier_probe(environment):  # noqa: F811
    api, _ = environment
    response = await api.post("/api/status/check")
    assert response.status_code == 200, response.text
    body = response.json()
    assert datetime.fromisoformat(body["checked_at"])
    assert body["checks"]["business_database"]["state"] == "available"
    assert body["checks"]["model_configuration"]["state"] == "unavailable"
    assert body["checks"]["model_provider"]["state"] == "not_checked"
    assert body["checks"]["model_provider"]["reason"]
    assert "secret" not in response.text.lower()


async def test_loopback_probe_distinguishes_reachable_and_unreachable():
    from app.api.status_checks import probe_loopback

    server = await asyncio.start_server(lambda _reader, writer: writer.close(),
                                        "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert await probe_loopback(f"ws://127.0.0.1:{port}") is True
    finally:
        server.close()
        await server.wait_closed()
    assert await probe_loopback(f"ws://127.0.0.1:{port}") is False
