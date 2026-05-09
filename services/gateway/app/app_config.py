from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # ClickHouse
    CLICKHOUSE_HOST: str = "clickhouse"
    CLICKHOUSE_PORT: int = 8123
    CLICKHOUSE_USER: str = "default"
    CLICKHOUSE_PASSWORD: str = ""
    CLICKHOUSE_DB: str = "default"
    
    # Models
    MODELS_PATH: str = "/app/services/models"
    
    # TriSQL Parameters (из статьи оптимальные значения)
    LAMBDA_COEF: float = 0.8  # баланс table/column attention
    TAU_THRESHOLD: float = 0.6  # порог фильтрации схемы
    
    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
