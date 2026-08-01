"""Vercel ASGI entry point for the Garment ERP API.

The browser uses a same-origin ``/api`` prefix.  The production FastAPI app
keeps its existing unprefixed routes, so this thin parent application mounts
it under that public prefix.

Two things differ from the long-lived server deployments (Docker/compose) and
are handled here rather than in ``app.main``:

* Vercel has no release phase, so nothing runs ``alembic upgrade head`` before
  traffic arrives.  The first cold start migrates the schema itself, serialised
  across concurrent instances by a Postgres advisory lock.
* Vercel's Python runtime does not reliably emit ASGI lifespan events, so the
  startup wiring (finance subscribers, chart of accounts, bootstrap admin) runs
  eagerly at import *as well as* from the lifespan hook.  Every step is
  idempotent, so running it twice is harmless and running it once is enough.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

logger = logging.getLogger("garment_erp.vercel")

# Serialises concurrent cold starts racing to migrate. The value is arbitrary
# but must be stable — any other advisory lock in the app must not reuse it.
_MIGRATION_LOCK_ID = 4_812_337_001

_bootstrapped = False


def _apply_serverless_defaults() -> None:
    """Fill in settings that only make sense on Vercel.

    Explicit Vercel project variables still take precedence — every value here
    goes through ``setdefault`` — and the database secret is always supplied
    through the project environment.
    """
    # The function filesystem is read-only apart from /tmp.
    os.environ.setdefault("ATTACHMENTS_DIR", "/tmp/garment-erp-attachments")

    production_host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "")
    # Preview deployments get a generated *.vercel.app hostname, so the
    # wildcard has to stay; the production alias is added explicitly because a
    # custom domain would not match it.
    allowed_hosts = ["*.vercel.app"]
    if production_host:
        allowed_hosts.append(production_host)
    os.environ.setdefault("ALLOWED_HOSTS", ",".join(allowed_hosts))

    # The SPA is served from the same origin, so CORS is not actually exercised
    # — but production config validation requires every entry to be HTTPS.
    origin_host = (
        production_host
        or os.environ.get("VERCEL_URL")
        or "garment-erp.vercel.app"
    )
    os.environ.setdefault("CORS_ORIGINS", f"https://{origin_host}")


def _normalize_database_url() -> None:
    """Pin the psycopg 3 driver on whatever connection string we were handed.

    Marketplace integrations (Neon, Supabase, …) inject a stock
    ``postgresql://…`` URL, which SQLAlchemy resolves to psycopg *2* — not
    installed; only psycopg 3 is in requirements.txt. Rewriting it here means
    the deployment does not depend on whoever set the variable remembering the
    ``+psycopg`` suffix. ``postgres://`` is accepted too: several providers
    still emit it and SQLAlchemy rejects it outright.
    """
    url = os.environ.get("DATABASE_URL", "")
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            os.environ["DATABASE_URL"] = f"postgresql+psycopg://{url[len(prefix):]}"
            return


_apply_serverless_defaults()
_normalize_database_url()

from app.main import app as erp_app  # noqa: E402


def _upgrade_schema() -> None:
    """Bring the database to the migration head.

    A no-op once the schema is current (one query against ``alembic_version``),
    which is the steady state — this only does real work on the deploy that
    ships a new migration.
    """
    from alembic import command
    from alembic.config import Config

    from app.config import settings
    from app.db import engine

    # Built programmatically rather than from alembic.ini so that alembic's
    # fileConfig does not replace the app's configured (JSON) logging.
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)

    if engine.dialect.name != "postgresql":
        command.upgrade(cfg, "head")
        return

    # Session-level lock on its own connection: alembic opens its own engine,
    # so the lock cannot ride along inside the migration transaction.
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "SELECT pg_advisory_lock(%(lock_id)s)", {"lock_id": _MIGRATION_LOCK_ID}
        )
        try:
            command.upgrade(cfg, "head")
        finally:
            connection.exec_driver_sql(
                "SELECT pg_advisory_unlock(%(lock_id)s)", {"lock_id": _MIGRATION_LOCK_ID}
            )


def _bootstrap_once() -> None:
    """Run the work ``app.main``'s lifespan would do on a long-lived server.

    Guarded so a cold start that *does* get lifespan events pays for it once.
    Failures are logged and swallowed: a function that cannot migrate should
    still boot and report the problem through ``/api/health``, rather than
    crash on import with no route to a diagnosis.
    """
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True

    from sqlmodel import Session

    from app.config import settings
    from app.db import engine, init_db
    from app.finance.posting import register_finance_subscribers
    from app.finance.service import seed_chart_of_accounts
    from app.security.service import bootstrap_admin

    try:
        if settings.auto_create_schema:
            init_db()
        elif os.environ.get("RUN_MIGRATIONS_ON_STARTUP", "1").lower() not in {
            "0",
            "false",
            "no",
        }:
            _upgrade_schema()

        # Without this the event seam is dead on Vercel: operations would post
        # no journals at all, silently.
        register_finance_subscribers()

        with Session(engine) as session:
            seed_chart_of_accounts(session)
            session.commit()
            bootstrap_admin(session)
    except Exception:
        logger.exception("Serverless bootstrap failed")


_bootstrap_once()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # If Vercel does emit lifespan events, _bootstrap_once has already run and
    # the child's own startup work is what remains.
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
    """Undo Vercel's rewrite of ``/api/<suffix>`` onto the function's own path.

    Vercel normally hands the function the original request path, in which case
    this is a no-op.  When it instead routes to the function's path and passes
    the captured suffix as a ``path`` query parameter, the mounted ERP app
    would see ``/api/index`` and 404 — so rebuild the original path first.
    """
    routed_path = request.query_params.get("path")
    function_paths = {"/api", "/api/index", "/api/index.py"}
    if request.scope["path"] in function_paths and routed_path:
        restored_path = f"/api/{routed_path.lstrip('/')}"
        request.scope["path"] = restored_path
        request.scope["raw_path"] = restored_path.encode("utf-8")
    return await call_next(request)


app.mount("/api", erp_app)
