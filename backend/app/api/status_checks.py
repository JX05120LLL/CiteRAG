"""Explicit free local checks. Supplier functional probes are never implicit."""

import asyncio
from urllib.parse import urlsplit


async def probe_loopback(url: str) -> bool:
    try:
        target = urlsplit(url)
        if (target.scheme not in {"ws", "wss"}
            or target.hostname not in {"127.0.0.1", "localhost", "::1"}
            or target.username or target.password or target.query or target.fragment
            or target.path not in {"", "/"} or target.port is None):
            return False
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(target.hostname, target.port), 1,
        )
        del reader
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 1)
        return True
    except (OSError, ValueError, TimeoutError):
        return False


def local_check(state: str, reason: str, checked_at: str) -> dict[str, str]:
    return {"state": state, "reason": reason, "checked_at": checked_at}
