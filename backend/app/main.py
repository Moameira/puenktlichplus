from datetime import datetime, timedelta
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.schemas import PredictionRequest, PredictionResponse, RouteOption
from app.services.cache import JsonCache
from app.services.collector import TimetableSnapshotStore
from app.services.connection_risk import ConnectionRiskCalculator
from app.services.data_source import DATA_DIR, DbTimetablesClient, DelayRepository
from app.services.delay_model import ExplainableDelayModel
from app.services.rate_limit import SlidingWindowRateLimiter


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)


def configured_origins() -> list[str]:
    raw = os.getenv("FRONTEND_ORIGINS", "*")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]

app = FastAPI(
    title="PuenktlichPlus API",
    description="Realistic NRW train arrival windows and transfer risk estimates.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

repository = DelayRepository()
model = ExplainableDelayModel(repository)
risk_calculator = ConnectionRiskCalculator()
cache = JsonCache(Path(DATA_DIR) / "cache")
db_client = DbTimetablesClient(cache)
db_rate_limiter = SlidingWindowRateLimiter(max_calls=20, window_seconds=60)
snapshot_store = TimetableSnapshotStore()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "db_credentials_configured": db_client.configured,
        "env_file_found": ENV_PATH.exists(),
        "collector": snapshot_store.summary(),
    }


@app.get("/routes", response_model=list[RouteOption])
def routes() -> list[dict]:
    return repository.routes()


@app.get("/db/stations")
async def db_stations(pattern: str = Query(min_length=2, max_length=80)) -> dict:
    if not db_client.configured:
        raise HTTPException(
            status_code=503,
            detail="DB credentials are not configured. Add DB_CLIENT_ID and DB_API_KEY to backend/.env.",
        )
    if not db_rate_limiter.allow():
        raise HTTPException(status_code=429, detail="Zu viele DB-Abfragen. Bitte kurz warten.")
    try:
        stations = await db_client.station_lookup(pattern)
    except httpx.HTTPStatusError as exc:
        detail = "DB API request failed."
        if exc.response.status_code == 403:
            detail = (
                "DB API returned 403. The application is probably not subscribed "
                "to the free Timetables plan yet."
            )
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    return {"source": "db-timetables", "pattern": pattern, "stations": stations[:12]}


@app.get("/db/next-trains")
async def db_next_trains(
    origin: str = Query(min_length=2, max_length=80),
    destination: str = Query(min_length=2, max_length=80),
) -> dict:
    if not db_client.configured:
        raise HTTPException(
            status_code=503,
            detail="DB credentials are not configured. Add DB_CLIENT_ID and DB_API_KEY to backend/.env.",
        )
    if not db_rate_limiter.allow():
        raise HTTPException(status_code=429, detail="Zu viele DB-Abfragen. Bitte kurz warten.")
    try:
        return await db_client.next_departures(origin, destination)
    except httpx.HTTPStatusError as exc:
        detail = "DB API request failed."
        if exc.response.status_code == 403:
            detail = (
                "DB API returned 403. The application is probably not subscribed "
                "to the free Timetables plan yet."
            )
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.get("/db/connections")
async def db_connections(
    origin: str = Query(min_length=2, max_length=80),
    destination: str = Query(min_length=2, max_length=80),
    offset_minutes: int = Query(default=0, ge=-180, le=360),
) -> dict:
    if not db_client.configured:
        raise HTTPException(
            status_code=503,
            detail="DB credentials are not configured. Add DB_CLIENT_ID and DB_API_KEY to backend/.env.",
        )
    if not db_rate_limiter.allow():
        raise HTTPException(status_code=429, detail="Zu viele DB-Abfragen. Bitte kurz warten.")
    try:
        search_time = datetime.now().astimezone() + timedelta(minutes=offset_minutes)
        return await db_client.connection_options(origin, destination, search_time)
    except httpx.HTTPStatusError as exc:
        detail = "DB API request failed."
        if exc.response.status_code == 403:
            detail = (
                "DB API returned 403. The application is probably not subscribed "
                "to the free Timetables plan yet."
            )
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/predict", response_model=PredictionResponse)
def predict(payload: PredictionRequest) -> PredictionResponse:
    predictions = [model.predict_leg(leg) for leg in payload.legs]
    distributions = [model.delay_distribution(leg) for leg in payload.legs]
    transfers = risk_calculator.calculate(payload.legs, distributions)
    return PredictionResponse(
        mode="live-db-plus-nrw-risk-model-v1",
        data_notice_de=(
            "DB Timetables liefert Bahnhofssuche und Live-Abfahrten. Das Umstiegsrisiko "
            "nutzt ein erklärbares NRW-Modell mit repräsentativen Verspätungsmustern."
        ),
        data_notice_en=(
            "Demo using a representative NRW dataset. DB's free Timetables API exposes live "
            "deviations, but not a free historical per-route archive."
        ),
        predictions=predictions,
        transfers=transfers,
    )
