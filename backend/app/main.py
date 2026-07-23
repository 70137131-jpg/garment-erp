from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlmodel import Session

from .config import settings
from .costing.router import router as costing_router
from .documents.router import router as documents_router
from .db import engine, init_db
from .finance.posting import register_finance_subscribers
from .finance.router import router as finance_router
from .finance.service import seed_chart_of_accounts
from .inventory.router import router as inventory_router
from .masters.router import router as masters_router
from .procurement.router import router as procurement_router
from .production.router import router as production_router
from .quality.router import router as quality_router
from .sales.router import router as sales_router
from .styles.router import router as styles_router
from .security.router import router as security_router
from .security.service import bootstrap_admin
from .security.middleware import SecurityMiddleware
from .kernel.rbac import Permission, require_permissions


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    version="0.1.0",
    description="Garment Manufacturing ERP — order-to-cash spine.",
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)

app.add_middleware(SecurityMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

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


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "app": settings.app_name}
