"""Proxy pool service (M3).

Supervised pool of residential proxies with health probes, sticky sessions,
graceful rotation. All scrapers lease from here; no one picks raw endpoints.
"""
