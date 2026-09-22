"""The account-free edition serves only the local installation's browser."""

from urllib.parse import urlsplit

from fastapi import Request

from app.config import Settings, is_loopback_host, is_loopback_ip
from app.services.errors import ServiceError


def local_host_header(value: str) -> bool:
    if not value or "\\" in value or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlsplit(f"http://{value}")
        return bool(
            parsed.hostname
            and is_loopback_host(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and value == parsed.netloc
            and (parsed.port is None or parsed.port > 0)
        )
    except ValueError:
        return False


def require_local_request(request: Request, settings: Settings) -> None:
    host_headers = request.headers.getlist("host")
    if (
        request.client is None
        or not is_loopback_ip(request.client.host)
        or len(host_headers) != 1
        or not local_host_header(host_headers[0])
        or any(
            name == "forwarded" or name.startswith("x-forwarded-")
            for name in request.headers
        )
    ):
        raise ServiceError(403, "local_only", "此服务仅允许本机直接访问")

    # Reject browser cross-site reads as well as writes, even when an Origin
    # header is absent (for example, a navigational or image request).
    fetch_sites = request.headers.getlist("sec-fetch-site")
    if len(fetch_sites) > 1 or (fetch_sites and fetch_sites[0] == "cross-site"):
        raise ServiceError(403, "origin_forbidden", "请求来源未获允许")
    origins = request.headers.getlist("origin")
    if (
        len(origins) > 1
        or (origins and origins[0] not in settings.allowed_origins)
        or (not origins and request.method not in {"GET", "HEAD", "OPTIONS"})
    ):
        raise ServiceError(403, "origin_forbidden", "请求来源未获允许")
