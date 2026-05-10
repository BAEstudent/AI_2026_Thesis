from contextlib import asynccontextmanager
from fastapi import FastAPI
from .routers import forecast
import clickhouse_connect
import os

def get_client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST"),
        port=os.getenv("CLICKHOUSE_HTTP_PORT"),
        username=os.getenv("CLICKHOUSE_USER"),
        password=os.getenv("CLICKHOUSE_PASSWORD")
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ch = get_client()
    yield


app = FastAPI(
    title="Seller Forecasting API",
    description="Serves pre-computed order forecasts for items, categories, and global trends.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(forecast.router)


@app.get("/health")
def health():
    return {"status": "ok"}