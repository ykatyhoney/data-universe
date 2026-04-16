"""Backend adapters — pure-function tests (no network)."""

from __future__ import annotations

import pytest

from proxy_pool.backends.static_list import (
    StaticListBackend,
    StaticListSettings,
    masked_url,
)
from proxy_pool.schemas import ProxyBackend


@pytest.mark.asyncio
async def test_static_list_empty_returns_nothing() -> None:
    backend = StaticListBackend(StaticListSettings(endpoints=""))
    assert await backend.load_endpoints() == []


@pytest.mark.asyncio
async def test_static_list_parses_multiple() -> None:
    settings = StaticListSettings(
        endpoints="http://u1:p1@h1:8080, http://u2:p2@h2:8081",
        supports_sticky=False,
    )
    backend = StaticListBackend(settings)
    eps = await backend.load_endpoints()
    assert len(eps) == 2
    assert eps[0].url == "http://u1:p1@h1:8080"
    assert eps[0].backend is ProxyBackend.STATIC_LIST
    assert eps[0].supports_sticky is False
    # IDs are deterministic + distinct.
    assert eps[0].id != eps[1].id


@pytest.mark.asyncio
async def test_static_list_stable_id_across_reloads() -> None:
    settings = StaticListSettings(endpoints="http://u:p@h:9000")
    b1 = StaticListBackend(settings)
    b2 = StaticListBackend(settings)
    assert (await b1.load_endpoints())[0].id == (await b2.load_endpoints())[0].id


@pytest.mark.asyncio
async def test_inject_session_adds_suffix_when_supported() -> None:
    backend = StaticListBackend(
        StaticListSettings(endpoints="http://user:pass@gate.example:8000", supports_sticky=True)
    )
    [ep] = await backend.load_endpoints()
    url = backend.inject_session(ep, "abc123")
    assert "user-session-abc123:pass@gate.example:8000" in url


@pytest.mark.asyncio
async def test_inject_session_noop_when_not_supported() -> None:
    backend = StaticListBackend(
        StaticListSettings(endpoints="http://user:pass@gate.example:8000", supports_sticky=False)
    )
    [ep] = await backend.load_endpoints()
    assert backend.inject_session(ep, "abc123") == ep.url


@pytest.mark.asyncio
async def test_inject_session_noop_when_no_username() -> None:
    """Sticky injection only makes sense for providers with a username; noop otherwise."""
    backend = StaticListBackend(
        StaticListSettings(endpoints="http://gate.example:8000", supports_sticky=True)
    )
    [ep] = await backend.load_endpoints()
    assert backend.inject_session(ep, "abc") == ep.url


def test_masked_url_hides_credentials() -> None:
    m = masked_url("http://hunter2_password_here:secret@host:8080")
    assert "hunter2" not in m
    assert "secret" not in m
    assert "host:8080" in m


def test_masked_url_no_creds_passthrough() -> None:
    assert masked_url("http://host:8080/path") == "http://host:8080/path"
