import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session

from .attachments.router import router as attachments_router
from .config import settings
from .observability import RequestLoggingMiddleware, configure_logging, init_sentry

configure_logging()
init_sentry()
logger = logging.getLogger("garment_erp.main")
from .costing.router import router as costing_router
from .documents.router import router as documents_router
from .db import engine, init_db
from .finance.posting import register_finance_subscribers
from .finance.router import router as finance_router
from .finance.service import seed_chart_of_accounts
from .imports.router import router as imports_router
from .inventory.router import router as inventory_router
from .masters.router import router as masters_router
from .procurement.router import router as procurement_router
from .production.router import router as production_router
from .quality.router import router as quality_router
from .sales.router import router as sales_router
from .styles.router import router as styles_router
from .tna.router import router as tna_router
from .workforce.router import router as workforce_router
from .security.router import router as security_router
from .security.service import bootstrap_admin
from .security.middleware import SecurityMiddleware
from .kernel.rbac import Permission, require_permissions


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Starting %s v%s (environment=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )
    if settings.auto_create_schema:
        init_db()
    # Wire finance auto-posting and ensure the default chart of accounts exists.
    register_finance_subscribers()
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        session.commit()
        bootstrap_admin(session)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Garment Manufacturing ERP — order-to-cash spine.",
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)

# First added = innermost: sees the request ID and principal set by outer layers.
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

# Outermost: compress sizeable JSON/CSV responses. Session tokens live in
# HttpOnly cookies (never in response bodies), so BREACH-style compression
# attacks have no secret to extract here.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(security_router)
app.include_router(masters_router, dependencies=[Depends(require_permissions(Permission.masters_read))])
app.include_router(styles_router, dependencies=[Depends(require_permissions(Permission.styles_read))])
app.include_router(inventory_router, dependencies=[Depends(require_permissions(Permission.inventory_read))])
app.include_router(sales_router, dependencies=[Depends(require_permissions(Permission.sales_read))])
app.include_router(procurement_router, dependencies=[Depends(require_permissions(Permission.procurement_read))])
app.include_router(quality_router, dependencies=[Depends(require_permissions(Permission.quality_read))])
app.include_router(production_router, dependencies=[Depends(require_permissions(Permission.production_read))])
app.include_router(costing_router, dependencies=[Depends(require_permissions(Permission.costing_read))])
app.include_router(finance_router, dependencies=[Depends(require_permissions(Permission.finance_read))])
app.include_router(documents_router, dependencies=[Depends(require_permissions(Permission.masters_read))])
app.include_router(imports_router)
app.include_router(attachments_router)
app.include_router(tna_router, dependencies=[Depends(require_permissions(Permission.sales_read))])
app.include_router(workforce_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log every unhandled error with its request ID and return a clean 500.

    The request ID in the response body lets a customer read the value off an
    error screen and quote it to support, who can grep the logs for it.
    """
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "Unhandled error on %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
        extra={"request_id": request_id},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
    )


def _migration_status() -> tuple[set[str], set[str]]:
    """(current DB revisions, migration head revisions)."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    heads = set(ScriptDirectory.from_config(cfg).get_heads())
    with engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    return current, heads


@app.get("/health", tags=["system"])
def health():
    """Liveness + database connectivity. Fails loudly when the DB is down."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check failed: database unreachable")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "app": settings.app_name, "database": "unreachable"},
        )
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "database": "ok",
    }


@app.get("/ready", tags=["system"])
def ready():
    """Readiness: DB reachable and schema at the expected migration head.

    With AUTO_CREATE_SCHEMA (dev/SQLite) there is no alembic_version table, so
    only connectivity is checked. Production deployments run migrations on
    boot, and a pod whose schema lags head must not receive traffic.
    """
    try:
        if settings.auto_create_schema:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return {"status": "ready", "migrations": "unmanaged (auto_create_schema)"}
        current, heads = _migration_status()
        if current != heads:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not-ready",
                    "migrations": {
                        "current": sorted(current),
                        "expected": sorted(heads),
                    },
                },
            )
        return {"status": "ready", "migrations": "up-to-date"}
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse(status_code=503, content={"status": "not-ready"})
