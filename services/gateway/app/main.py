from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
from contextlib import asynccontextmanager
from pydantic_settings import BaseSettings

from services.nlq_to_sql.app.app_config import settings
from services.nlq_to_sql.schemas import NLQueryRequest, NLQueryResponse
from app.services.nla_to_sql.nlq_to_sql import NLQToSQLService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

nlq_service: Optional[NLQToSQLService] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global nlq_service
    logger.info("🔄 Initializing NLQToSQL Service...")
    nlq_service = NLQToSQLService(
        host=settings.CLICKHOUSE_HOST,
        port=settings.CLICKHOUSE_PORT,
        user=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD,
        database=settings.CLICKHOUSE_DB,
        models_path=settings.MODELS_PATH
    )
    logger.info("✅ NLQToSQL Service initialized")
    yield
    logger.info("👋 Shutting down NLQToSQL Service...")

app = FastAPI(
    title="NLQToSQL API",
    description="Natural Language to SQL generation using TriSQL framework",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {"status": "healthy", "service": "nlq-to-sql"}

@app.post("/query", response_model=NLQueryResponse)
async def natural_language_query(request: NLQueryRequest):
    """
    Основной эндпоинт: преобразование естественного языка в SQL и выполнение
    
    - **question**: Вопрос на естественном языке
    - **auto_refine**: Автоматически исправлять ошибки выполнения (по умолчанию True)
    """
    if nlq_service is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    try:
        logger.info(f"📝 Processing question: {request.question[:100]}...")
        
        result = nlq_service.ask(
            question=request.question,
            auto_refine=request.auto_refine
        )
        
        logger.info(f"✅ Query completed: success={result['success']}, refined={result['refined']}")
        
        return NLQueryResponse(
            sql=result['sql'],
            success=result['success'],
            result=result['result'],
            error=result['error'] if not result['success'] else None,
            refined=result['refined']
        )
    
    except Exception as e:
        logger.error(f"❌ Error processing query: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/schema")
async def get_schema():
    """Получить текущую схему базы данных"""
    if nlq_service is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    return {"schema": nlq_service.get_schema_summary()}

@app.get("/metrics")
async def get_metrics():
    """Статистика использования сервиса"""
    if nlq_service is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    return nlq_service.get_metrics()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
