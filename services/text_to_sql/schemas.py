from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

class NLQueryRequest(BaseModel):
    question: str = Field(..., description="Вопрос на естественном языке", min_length=1)
    auto_refine: bool = Field(True, description="Автоматически исправлять ошибки SQL")

class NLQueryResponse(BaseModel):
    sql: str = Field(..., description="Сгенерированный SQL запрос")
    success: bool = Field(..., description="Успешность выполнения")
    result: Optional[Any] = Field(None, description="Результат выполнения запроса")
    error: Optional[str] = Field(None, description="Ошибка при выполнении")
    refined: bool = Field(False, description="Был ли запрос отрефайнен")

class HealthResponse(BaseModel):
    status: str
    service: str

class SchemaSummary(BaseModel):
    tables: List[str]
    total_columns: int
