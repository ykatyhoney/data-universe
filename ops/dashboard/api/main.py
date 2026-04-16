"""Dashboard API — FastAPI backend + static UI.

Surfaces:
- ``/api/health``            liveness + Redis + Postgres reachability (no auth)
- ``/api/auth/login|logout``  single-user cookie auth
- ``/api/overview``          homepage summary counters
- ``/api/proxies``           proxy-pool state
- ``/api/accounts``          account-pool state
- ``/api/workers``           worker fleet
- ``/api/tasks``             recent tasks
- ``/api/metrics/summary``   rolling time-series for the dashboard charts
- ``/ws/live``               broadcast of typed events from Redis pub/sub
- ``/metrics``               Prometheus format, canonical registry (no auth)
- ``/``, ``/static/*``       Miner Control Room UI
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from account_pool.crypto import CookieSealer, CookieSealError
from account_pool.service import AccountPoolService
from account_pool.service import set_service as set_account_service
from dashboard.api.auth import is_valid_cookie
from dashboard.api.metric_poller import poller
from dashboard.api.retention import sweeper
from dashboard.api.routes import (
    account_pool_routes,
    auth_routes,
    fleet,
    metrics_routes,
    overview,
    proxy_pool_routes,
    reddit_routes,
    tasks_routes,
    worker_routes,
)
from dashboard.api.ws import hub
from proxy_pool.backends.static_list import StaticListBackend
from proxy_pool.health import HealthProber
from proxy_pool.service import ProxyPoolService, set_service
from shared.clock import now_utc
from shared.config import get_settings
from shared.infra import dispose_engine, dispose_redis, ping_database, ping_redis
from shared.logging import configure_logging, get_logger
from shared.metrics import registry as canonical_registry

configure_logging()
log = get_logger(__name__)

api_requests_total = Counter(
    "api_requests_total",
    "HTTP requests handled by the dashboard API.",
    labelnames=("route", "method"),
    registry=canonical_registry,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log.info("dashboard_api.start", service=settings.service_name)
    await hub.start()
    await poller.start()
    await sweeper.start()

    # Proxy pool: construct with all backends, sync DB, then start health prober.
    proxy_service = ProxyPoolService(backends=[StaticListBackend()])
    await proxy_service.sync_from_backends()
    set_service(proxy_service)
    proxy_health = HealthProber(proxy_service)
    await proxy_health.start()

    # Account pool: needs a cookie sealer + proxy_service to pair leases.
    # Degrades gracefully if OPS_ACCOUNT_POOL_KEY is missing — the routes
    # will 503 but the rest of the app stays up.
    sealer: CookieSealer | None
    try:
        sealer = CookieSealer.from_env()
    except CookieSealError as e:
        log.warning("account_pool.sealer_unavailable", error=str(e))
        sealer = None
    if sealer is not None:
        account_service = AccountPoolService(sealer=sealer, proxy_pool=proxy_service)
        set_account_service(account_service)
    else:
        log.warning(
            "account_pool.disabled",
            hint="set OPS_ACCOUNT_POOL_KEY (run: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')",
        )

    try:
        yield
    finally:
        log.info("dashboard_api.stop")
        set_account_service(None)
        await proxy_health.stop()
        set_service(None)
        await sweeper.stop()
        await poller.stop()
        await hub.stop()
        await dispose_redis()
        await dispose_engine()


app = FastAPI(
    title="Data Universe Ops API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth_routes.router)
app.include_router(overview.router)
app.include_router(fleet.router)
app.include_router(tasks_routes.router)
app.include_router(metrics_routes.router)
app.include_router(proxy_pool_routes.router)
app.include_router(account_pool_routes.router)
app.include_router(reddit_routes.router)
app.include_router(worker_routes.router)

_WEB_DIR = Path(__file__).resolve().parents[1] / "web"
_DIST_DIR = _WEB_DIR / "dist"
_BUILT = (_DIST_DIR / "index.html").is_file()


@app.get("/api/health")
async def health() -> JSONResponse:
    api_requests_total.labels(route="/api/health", method="GET").inc()
    db_ok = await ping_database()
    rd_ok = await ping_redis()
    overall_ok = db_ok and rd_ok
    return JSONResponse(
        {
            "status": "ok" if overall_ok else "degraded",
            "service": get_settings().service_name,
            "ts": now_utc().isoformat(),
            "milestone": "M5",
            "database": "ok" if db_ok else "down",
            "redis": "ok" if rd_ok else "down",
        },
        status_code=200 if overall_ok else 503,
    )


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(
        generate_latest(canonical_registry).decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket) -> None:
    cookie = ws.cookies.get("ops_session")
    if not is_valid_cookie(cookie):
        await ws.close(code=4401)
        return
    await ws.accept()
    async with hub.register(ws) as client:
        with contextlib.suppress(WebSocketDisconnect):
            await hub.client_loop(client)


# ----- static UI mount (keep last so /api and /ws take precedence) ----- #


_FALLBACK_HTML = (
    "<!doctype html><html><body style='font-family:sans-serif;padding:2rem;"
    "background:#0b0d10;color:#d7dde5'>"
    "<h1>Frontend not built</h1>"
    "<p>Run <code>make build-web</code> (or <code>npm install &amp;&amp; npm run build</code> "
    "in <code>ops/dashboard/web/</code>) to produce <code>dist/</code>.</p>"
    "<p>API is live: <a href='/api/health' style='color:#4ade80'>/api/health</a> · "
    "<a href='/metrics' style='color:#4ade80'>/metrics</a></p></body></html>"
)


@app.get("/", response_model=None)
async def index() -> FileResponse | PlainTextResponse:
    api_requests_total.labels(route="/", method="GET").inc()
    if _BUILT:
        return FileResponse(_DIST_DIR / "index.html")
    return PlainTextResponse(_FALLBACK_HTML, media_type="text/html", status_code=200)


if _BUILT:
    app.mount("/assets", StaticFiles(directory=str(_DIST_DIR / "assets")), name="assets")
