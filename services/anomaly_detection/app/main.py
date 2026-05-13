import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import reports, health
from app.services.model_loader import ModelLoader
from app.services.clickhouse_client import ClickHouseClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared resources on startup and clean up on shutdown."""
    logger.info("Starting Anomaly Detection Service...")

    # Initialize ClickHouse client
    app.state.ch_client = ClickHouseClient(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        database=settings.clickhouse_database,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )
    await app.state.ch_client.connect()
    logger.info("ClickHouse connection established.")

    app.state.model_loader = ModelLoader(models_dir=settings.models_dir)
    app.state.model_loader.load_all()
    logger.info(f"Loaded models: {list(app.state.model_loader.models.keys())}")

    yield

    await app.state.ch_client.close()
    logger.info("Anomaly Detection Service shut down.")


app = FastAPI(
    title="Anomaly Detection Service",
    description="Detects anomalies in e-commerce analytics data stored in ClickHouse.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["Health"])
app.include_router(reports.router, prefix="/reports", tags=["Reports"])