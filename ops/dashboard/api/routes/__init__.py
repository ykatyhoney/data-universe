"""REST route modules. Each file exports an ``APIRouter`` named ``router``."""

from . import (
    account_pool_routes,
    fleet,
    metrics_routes,
    overview,
    proxy_pool_routes,
    reddit_routes,
    tasks_routes,
    worker_routes,
)
from . import auth as auth_routes

__all__ = [
    "account_pool_routes",
    "auth_routes",
    "fleet",
    "metrics_routes",
    "overview",
    "proxy_pool_routes",
    "reddit_routes",
    "tasks_routes",
    "worker_routes",
]
