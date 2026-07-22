import hmac
import secrets
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import settings


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class SecurityMiddleware:
    """Enforce browser-request invariants and attach defensive headers."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length:
            try:
                too_large = int(content_length) > settings.max_request_body_bytes
            except ValueError:
                too_large = True
            if too_large:
                response = JSONResponse({"detail": "Request body is too large"}, status_code=413)
                await response(scope, receive, send)
                return

        method = scope.get("method", "GET").upper()
        authorization = headers.get("authorization", "")
        if method in UNSAFE_METHODS and not authorization.lower().startswith("bearer "):
            cookies = _parse_cookies(headers.get("cookie", ""))
            cookie_token = cookies.get(settings.csrf_cookie_name, "")
            header_token = headers.get("x-csrf-token", "")
            if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
                response = JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
                await response(scope, receive, send)
                return

        supplied_request_id = headers.get("x-request-id", "")
        try:
            request_id = str(uuid.UUID(supplied_request_id))
        except (ValueError, AttributeError):
            request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["Referrer-Policy"] = "no-referrer"
                response_headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                path = scope.get("path", "")
                if settings.enable_api_docs and path in {"/docs", "/redoc"}:
                    # FastAPI's development-only documentation loads its UI
                    # assets from jsDelivr. Production docs are disabled.
                    response_headers["Content-Security-Policy"] = (
                        "default-src 'none'; script-src https://cdn.jsdelivr.net 'unsafe-inline'; "
                        "style-src https://cdn.jsdelivr.net 'unsafe-inline'; img-src data:; "
                        "frame-ancestors 'none'; base-uri 'none'"
                    )
                else:
                    response_headers["Content-Security-Policy"] = (
                        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
                        "form-action 'self'"
                    )
                if path.startswith("/auth"):
                    response_headers["Cache-Control"] = "no-store"
                    response_headers["Pragma"] = "no-cache"
                if settings.session_cookie_secure:
                    response_headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _parse_cookies(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        result[name] = value
    return result


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response, token: str | None = None) -> str:
    token = token or new_csrf_token()
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    return token
