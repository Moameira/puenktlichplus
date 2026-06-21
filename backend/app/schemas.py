from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class LegRequest(BaseModel):
    origin: str
    destination: str
    line: Optional[str] = None
    scheduled_departure: datetime
    scheduled_arrival: datetime


class PredictionRequest(BaseModel):
    legs: List[LegRequest] = Field(min_length=1, max_length=5)


class ArrivalWindow(BaseModel):
    earliest: datetime
    likely: datetime
    latest: datetime
    pessimistic: datetime


class LegPrediction(BaseModel):
    origin: str
    destination: str
    line: str
    scheduled_arrival: datetime
    expected_delay_minutes: float
    arrival_window: ArrivalWindow
    confidence: str
    sample_size: int
    explanation: str


class TransferRisk(BaseModel):
    from_leg: int
    to_leg: int
    station: str
    planned_buffer_minutes: float
    miss_probability: float
    risk_level: str
    message_de: str
    message_en: str


class PredictionResponse(BaseModel):
    mode: str
    data_notice_de: str
    data_notice_en: str
    predictions: List[LegPrediction]
    transfers: List[TransferRisk]


class RouteOption(BaseModel):
    origin: str
    destination: str
    line: str
    typical_minutes: int
