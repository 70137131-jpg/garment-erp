from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from .config import settings
from .costing.router import router as costing_router
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Wire finance auto-posting and ensure the default chart of accounts exists.
    register_finance_subscribers()
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        session.commit()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Garment Manufacturing ERP — order-to-cash spine.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(masters_router)
app.include_router(styles_router)
app.include_router(inventory_router)
app.include_router(sales_router)
app.include_router(procurement_router)
app.include_router(quality_router)
app.include_router(production_router)
app.include_router(costing_router)
app.include_router(finance_router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "app": settings.app_name}
