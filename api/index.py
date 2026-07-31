"""Vercel ASGI entry point for the Garment ERP API.

The browser uses a same-origin ``/api`` prefix.  The production FastAPI app
keeps its existing unprefixed routes, so this thin parent application mounts
it under that public prefix while still running the child's lifespan hooks.
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI


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
app.mount("/api", erp_app)
