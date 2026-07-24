"""Structured logging, request access logs, and optional Sentry error tracking.

Every log record carries the request ID minted by SecurityMiddleware so a
support engineer can join application logs, access logs, and the X-Request-ID
a customer reads off an error screen.
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import settings

logger = logging.getLogger("garment_erp")
access_logger = logging.getLogger("garment_erp.access")


class JsonFormatter(logging.Formatter):
    """One JSON object per line — what Loki/CloudWatch/`docker logs` + jq expect."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for attr in ("request_id", "user_id", "method", "path", "status_code", "duration_ms", "client_ip"):
            value = getattr(record, attr, None)
            if value is not None:
                payload[attr] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single-line format for local development."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        request_id = getattr(record, "request_id", None)
        if request_id:
            base += f" [req={request_id[:8]}]"
        return base


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            ConsoleFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    # Uvicorn's own access log duplicates our richer access log; keep its
    # error/startup messages but route everything through the root handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers = []
        uv.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def init_sentry() -> None:
    """Enable Sentry when a DSN is configured. A missing SDK is only an error
    if the deployment actually asked for Sentry."""
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RuntimeError(
            "SENTRY_DSN is set but sentry-sdk is not installed (pip install sentry-sdk)"
        ) from exc
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.app_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )
    logger.info("Sentry error tracking enabled")


class RequestLoggingMiddleware:
    """Access log with request ID, user, status, and duration.

    Added innermost (first ``add_middleware`` call) so it observes the request
    ID and principal that outer middleware/dependencies attach to the scope.
    """

    QUIET_PATHS = {"/health", "/ready"}

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_capture)
        finally:
            path = scope.get("path", "")
            state = scope.get("state", {})
            principal = getattr(state, "principal", None) if not isinstance(state, dict) else state.get("principal")
            client = scope.get("client")
            level = logging.DEBUG if path in self.QUIET_PATHS else logging.INFO
            access_logger.log(
                level,
                "%s %s -> %d",
                scope.get("method", "-"),
                path,
                status_code,
                extra={
                    "request_id": state.get("request_id") if isinstance(state, dict) else getattr(state, "request_id", None),
                    "user_id": getattr(principal, "user_id", None),
                    "method": scope.get("method"),
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client_ip": client[0] if client else None,
                },
            )
