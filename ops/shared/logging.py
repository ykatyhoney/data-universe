from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from .config import get_settings


def configure_logging() -> None:
    """Set up structlog → JSON → stdout. Called once at service startup.

    Every service must call this in its entry point. Output is consumed by
    Promtail (M2) and shipped to Loki. Never log secrets — a CI test in M4
    will grep staging logs for cookie/auth substrings.
    """
    settings = get_settings()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level,
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(settings.log_level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bind service name into every log line.
    structlog.contextvars.bind_contextvars(service=settings.service_name)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
