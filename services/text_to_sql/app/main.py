import os
import logging
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import clickhouse_connect
from .model_inference import TriSQLInference

logger = logging.getLogger("uvicorn")

# ── Configuration from environment ──
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "clickhouse")
CLICKHOUSE_HTTP_PORT = int(os.getenv("CLICKHOUSE_HTTP_PORT", 8123))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DB", "analytics")
MODEL_DIR = os.getenv("MODEL_DIR", "/app/models")

# ── ClickHouse client ──
class ClickHouseClient:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = clickhouse_connect.get_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_HTTP_PORT,
                username=CLICKHOUSE_USER,
                password=CLICKHOUSE_PASSWORD,
                database=CLICKHOUSE_DB,
            )
        return self._client

    def query(self, sql: str) -> Dict[str, Any]:
        client = self._get_client()
        result = client.query(sql)
        return {
            "columns": result.column_names,
            "rows": result.result_rows,
        }

    def get_schema(self) -> Dict[str, List[str]]:
        """
        Fetch all tables and their columns from the current database.
        Skips internal ClickHouse tables (those starting with '.inner').
        """
        client = self._get_client()
        schema = {}

        # Get user table names from the current database
        tables_query = f"""
            SELECT name
            FROM system.tables
            WHERE database = '{CLICKHOUSE_DB}'
        """
        tables_result = client.query(tables_query)
        table_names = [row[0] for row in tables_result.result_rows]

        for table in table_names:
            # Skip materialized view internal tables if desired (optional)
            if table.startswith(".inner"):
                continue

            columns_query = f"""
                SELECT name
                FROM system.columns
                WHERE database = '{CLICKHOUSE_DB}'
                  AND table = '{table}'
            """
            cols_result = client.query(columns_query)
            columns = [row[0] for row in cols_result.result_rows]
            if columns:   # only add if we got columns
                schema[table] = columns

        return schema

ch_client = ClickHouseClient()

# ── TriSQL inference instance (lazy init) ──
model = None
CURRENT_SCHEMA = {}

async def load_model_and_schema():
    """Called on startup. Loads TriSQL model and fetches schema."""
    global model, CURRENT_SCHEMA

    # Load schema from ClickHouse (retry once if connection fails)
    try:
        CURRENT_SCHEMA = ch_client.get_schema()
        logger.info(f"Schema loaded: {list(CURRENT_SCHEMA.keys())}")
    except Exception as e:
        logger.error(f"Could not load schema on startup: {e}")
        raise RuntimeError("Schema loading failed – service cannot start.") from e

    # Load TriSQL models
    model = TriSQLInference(MODEL_DIR)
    logger.info("TriSQL model loaded.")

# ── FastAPI app ──
app = FastAPI(title="Text-to-SQL Service (ClickHouse)")

@app.on_event("startup")
async def startup_event():
    await load_model_and_schema()

# ── Request / Response ──
class QueryRequest(BaseModel):
    question: str

class QueryResponse(BaseModel):
    sql: str
    columns: List[str]
    rows: List[List[Any]]
    row_count: int

# ── Endpoint ──
@app.post("/api/v1/query", response_model=QueryResponse)
def query(request: QueryRequest):
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is empty.")

    # 1. Generate SQL using TriSQL and current schema
    result = model.predict(question, CURRENT_SCHEMA)
    sql = result["sql"]

    # 2. Safety: only SELECT
    if not sql.strip().upper().startswith("SELECT"):
        raise HTTPException(status_code=400, detail="Only SELECT queries are supported.")

    # 3. Execute on ClickHouse
    try:
        data = ch_client.query(sql)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ClickHouse error: {str(e)}")

    columns = data["columns"]
    rows = [list(row) for row in data["rows"]]

    return QueryResponse(
        sql=sql,
        columns=columns,
        rows=rows,
        row_count=len(rows),
    )

@app.get("/health")
def health():
    return {"status": "ok", "tables": list(CURRENT_SCHEMA.keys())}
