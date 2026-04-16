"""Protocol every proxy backend implements.

Keep this surface tight: ``load_endpoints`` + ``inject_session``. Anything
richer (country selection, bandwidth tiers, per-session revenue tracking)
hangs off provider-specific extensions, not the core protocol.
"""

from __future__ import annotations

from typing import Protocol

from proxy_pool.schemas import ProxyEndpoint


class ProxyBackendAdapter(Protocol):
    """Pluggable proxy source. ``name`` matches :class:`ProxyBackend` enum."""

    name: str

    async def load_endpoints(self) -> list[ProxyEndpoint]:
        """Return the set of endpoints this backend currently exposes.

        Called on service start + periodically (hourly). Providers with a
        stable endpoint list (static_list) return the same set every time;
        rotating residentials return fresh gateways.
        """
        ...

    def inject_session(self, endpoint: ProxyEndpoint, session_id: str) -> str:
        """Return a URL with ``session_id`` encoded into it (usually via the
        username suffix, e.g. ``user-session-XYZ``). For backends that don't
        support sticky sessions, returns ``endpoint.url`` unchanged.
        """
        ...
