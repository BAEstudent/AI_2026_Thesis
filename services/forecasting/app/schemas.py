from datetime import date, datetime
from pydantic import BaseModel


class ForecastPoint(BaseModel):
    ds:   date
    yhat: float


class ForecastResponse(BaseModel):
    granularity: str
    freq:        str
    series_id:   str
    model:       str
    computed_at: datetime
    points:      list[ForecastPoint]


class ModelScore(BaseModel):
    model: str
    MAE:   float
    RMSE:  float
    MAPE:  float
    SMAPE: float
