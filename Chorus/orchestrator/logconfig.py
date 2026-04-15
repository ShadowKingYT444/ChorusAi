"""Centralized logging config + request-id context middleware."""

from __future__ import annotations

import contextvars
import logging
import os
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def get_request_id() -> str:
    return request_id_var.get()


def set_request_id(value: str) -> contextvars.Token:
    return request_id_var.set(value)


class LogFilter(logging.Filter):
    """Inject request_id from contextvar onto every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.request_id = request_id_var.get()
        return True


_CONFIGURED = False


def configure_logging() -> None:
    """Configure root logger once. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    handler.addFilter(LogFilter())

    root = logging.getLogger()
    # Replace existing handlers so we don't double-log in reload scenarios.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)

    _CONFIGURED = True


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Read/generate X-Request-ID, bind to contextvar for log correlation."""

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("X-Request-ID")
        rid = incoming.strip() if incoming and incoming.strip() else uuid.uuid4().hex[:12]
        token = set_request_id(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response
