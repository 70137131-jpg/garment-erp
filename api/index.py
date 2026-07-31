"""Vercel ASGI entry point for the Garment ERP API.

The browser uses a same-origin ``/api`` prefix.  The production FastAPI app
keeps its existing unprefixed routes, so this thin parent application mounts
it under that public prefix while still running the child's lifespan hooks.
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

# Safe serverless defaults. Explicit Vercel project variables still take
# precedence, and the database secret is always supplied through Vercel env.
os.environ.setdefault("ATTACHMENTS_DIR", "/tmp/garment-erp-attachments")
os.environ.setdefault("ALLOWED_HOSTS", "*.vercel.app")
production_host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "garment-erp.vercel.app")
os.environ.setdefault("CORS_ORIGINS", f"https://{production_host}")

from app.main import app as erp_app  # noqa: E402


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with erp_app.router.lifespan_context(erp_app):
        yield


app = FastAPI(
    title="Garment ERP Gateway",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def restore_api_path_after_vercel_rewrite(request: Request, call_next):
    """Restore the API suffix captured by Vercel's function rewrite.

    Vercel routes ``/api/<suffix>`` to this Python function as
    ``/api/index.py?path=<suffix>``.  The ERP application is mounted at
    ``/api``, so restore the original path before its router handles the
    request.  This keeps the browser's same-origin API contract intact.
    """
    routed_path = request.query_params.get("path")
    if request.scope["path"] == "/api/index.py" and routed_path:
        restored_path = f"/api/{routed_path.lstrip('/')}"
        request.scope["path"] = restored_path
        request.scope["raw_path"] = restored_path.encode("utf-8")
    return await call_next(request)


app.mount("/api", erp_app)
