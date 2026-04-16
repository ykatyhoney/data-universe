"""Development + small-scale production backend.

Reads ``OPS_PROXY_STATIC_ENDPOINTS`` (comma-separated URLs). Supports
session injection when the URL uses a residential-style username
(``user-session-XYZ``) — we append ``-session-<id>`` to the username.

Example env::

    OPS_PROXY_STATIC_ENDPOINTS=http://user1:pass1@proxy1.example:8080,http://user2:pass2@proxy2.example:8080
    OPS_PROXY_STATIC_SUPPORTS_STICKY=true
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from proxy_pool.schemas import ProxyBackend, ProxyEndpoint


class StaticListSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPS_PROXY_STATIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    endpoints: str = Field(default="", description="comma-separated proxy URLs")
    supports_sticky: bool = Field(default=False)


def _endpoint_id(url: str) -> str:
    """Stable 16-hex id derived from the URL (credentials included).

    Used as the primary key in ``ops.proxies`` so a re-sync from the same
    backend updates the same rows instead of creating duplicates.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _mask(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        netloc = f"{parts.username[:3]}…@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class StaticListBackend:
    """Endpoints come from an env var; no external API calls."""

    name = ProxyBackend.STATIC_LIST.value

    def __init__(self, settings: StaticListSettings | None = None) -> None:
        self._settings = settings or StaticListSettings()

    async def load_endpoints(self) -> list[ProxyEndpoint]:
        raw = self._settings.endpoints.strip()
        if not raw:
            return []
        endpoints: list[ProxyEndpoint] = []
        for url in [u.strip() for u in raw.split(",") if u.strip()]:
            endpoints.append(
                ProxyEndpoint(
                    id=_endpoint_id(url),
                    url=url,
                    backend=ProxyBackend.STATIC_LIST,
                    supports_sticky=self._settings.supports_sticky,
                )
            )
        return endpoints

    def inject_session(self, endpoint: ProxyEndpoint, session_id: str) -> str:
        if not endpoint.supports_sticky:
            return endpoint.url
        parts = urlsplit(endpoint.url)
        if not parts.username:
            return endpoint.url
        new_user = f"{parts.username}-session-{session_id}"
        password = parts.password or ""
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        netloc = f"{new_user}:{password}@{host}{port}" if password else f"{new_user}@{host}{port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def masked_url(url: str) -> str:
    """Exposed as a helper so ``service.py`` can mask any URL identically."""
    return _mask(url)
