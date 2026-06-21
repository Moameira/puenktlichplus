import csv
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx

from app.services.cache import JsonCache


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BERLIN_TZ = ZoneInfo("Europe/Berlin")


@dataclass(frozen=True)
class DelayObservation:
    origin: str
    destination: str
    line: str
    dep_hour: int
    day_of_week: int
    delay_minutes: int


class DelayRepository:
    def __init__(self, csv_path: Path = DATA_DIR / "nrw_delay_observations.csv") -> None:
        self.csv_path = csv_path
        self._observations: Optional[List[DelayObservation]] = None

    def observations(self) -> List[DelayObservation]:
        if self._observations is None:
            with self.csv_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self._observations = [
                    DelayObservation(
                        origin=row["origin"],
                        destination=row["destination"],
                        line=row["line"],
                        dep_hour=int(row["dep_hour"]),
                        day_of_week=int(row["day_of_week"]),
                        delay_minutes=int(row["delay_minutes"]),
                    )
                    for row in reader
                ]
        return self._observations

    def routes(self) -> List[Dict[str, object]]:
        grouped: Dict[tuple, List[DelayObservation]] = {}
        for item in self.observations():
            grouped.setdefault((item.origin, item.destination, item.line), []).append(item)

        routes = []
        for (origin, destination, line), rows in grouped.items():
            typical_minutes = {
                "RE1": 84,
                "RE5": 68,
                "RE6": 63,
                "RE7": 56,
                "S11": 34,
                "IC2045": 95,
            }.get(line, 45)
            routes.append(
                {
                    "origin": origin,
                    "destination": destination,
                    "line": line,
                    "typical_minutes": typical_minutes,
                }
            )
        return sorted(routes, key=lambda route: (str(route["origin"]), str(route["destination"])))


class DbTimetablesClient:
    """Thin adapter for DB's free Timetables API; useful for live boards, not historical archives."""

    BASE_URL = "https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1"

    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache
        self.client_id = os.getenv("DB_CLIENT_ID")
        self.api_key = os.getenv("DB_API_KEY")

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.api_key)

    async def station_lookup(self, pattern: str) -> List[dict]:
        cache_key = f"station-{pattern.lower().replace(' ', '-')}"
        cached = self.cache.get(cache_key)
        if cached is not None and (cached or self.germanize_station_query(pattern) == pattern):
            return cached
        if not self.configured:
            return []

        headers = {"DB-Client-Id": self.client_id or "", "DB-Api-Key": self.api_key or ""}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.BASE_URL}/station/{pattern}", headers=headers)
            response.raise_for_status()
        value = self.parse_stations(response.text)
        if not value:
            fallback_pattern = self.germanize_station_query(pattern)
            if fallback_pattern != pattern:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(f"{self.BASE_URL}/station/{fallback_pattern}", headers=headers)
                    response.raise_for_status()
                value = self.parse_stations(response.text)
        self.cache.set(cache_key, value)
        return value

    async def next_departures(self, origin: str, destination: str, when: Optional[datetime] = None) -> dict:
        if not self.configured:
            return {"origin": None, "destination": None, "departures": []}

        origin_station = await self._best_station(origin)
        destination_station = await self._best_station(destination)
        if not origin_station or not destination_station:
            return {"origin": origin_station, "destination": destination_station, "departures": []}

        moment = when or datetime.now(BERLIN_TZ)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=BERLIN_TZ)
        departures = []
        for offset in range(3):
            hour = moment + timedelta(hours=offset)
            board = await self._station_plan(origin_station["eva"], hour)
            departures.extend(self.parse_departures(board, origin_station, destination_station))

        future_departures = [
            departure for departure in departures if departure["scheduled_departure"] >= moment.isoformat()
        ]
        unique = {}
        for departure in sorted(future_departures, key=lambda item: item["scheduled_departure"]):
            key = (departure["train"], departure["scheduled_departure"])
            unique.setdefault(key, departure)

        return {
            "origin": origin_station,
            "destination": destination_station,
            "departures": list(unique.values())[:2],
        }

    @staticmethod
    def parse_stations(xml_text: str) -> List[dict]:
        root = ET.fromstring(xml_text)
        stations = []
        for station in root.iter():
            if not station.tag.endswith("station"):
                continue
            stations.append(
                {
                    "name": station.attrib.get("name", ""),
                    "eva": station.attrib.get("eva", ""),
                    "ds100": station.attrib.get("ds100", ""),
                }
            )
        return stations

    async def _best_station(self, pattern: str) -> Optional[dict]:
        stations = await self.station_lookup(pattern)
        if not stations:
            return None
        exact = [station for station in stations if station["name"].lower() == pattern.lower()]
        return exact[0] if exact else stations[0]

    async def _station_plan(self, eva: str, moment: datetime) -> str:
        date = moment.strftime("%y%m%d")
        hour = moment.strftime("%H")
        cache_key = f"plan-{eva}-{date}-{hour}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return str(cached)

        headers = {"DB-Client-Id": self.client_id or "", "DB-Api-Key": self.api_key or ""}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.BASE_URL}/plan/{eva}/{date}/{hour}", headers=headers)
            response.raise_for_status()
        self.cache.set(cache_key, response.text)
        return response.text

    @staticmethod
    def parse_departures(xml_text: str, origin: dict, destination: dict) -> List[dict]:
        if not xml_text.strip():
            return []
        root = ET.fromstring(xml_text)
        departures = []
        destination_name = destination["name"].lower()

        for stop in root.iter():
            if not stop.tag.endswith("s"):
                continue
            timeline = next((child for child in stop if child.tag.endswith("tl")), None)
            departure = next((child for child in stop if child.tag.endswith("dp")), None)
            if departure is None:
                continue

            path = departure.attrib.get("ppth", "")
            path_stations = [item.strip() for item in path.split("|") if item.strip()]
            if destination_name not in [station.lower() for station in path_stations]:
                continue

            planned_time = DbTimetablesClient._parse_db_time(departure.attrib.get("pt", ""))
            if planned_time is None:
                continue

            line = departure.attrib.get("l", "")
            train_number = timeline.attrib.get("n", "") if timeline is not None else ""
            train_category = timeline.attrib.get("c", "") if timeline is not None else ""
            train = " ".join(part for part in [train_category, train_number] if part).strip()
            departures.append(
                {
                    "origin": origin["name"],
                    "destination": destination["name"],
                    "line": line or train or "DB",
                    "train": train or line or "DB",
                    "platform": departure.attrib.get("pp", ""),
                    "scheduled_departure": planned_time.isoformat(),
                    "path": path_stations,
                }
            )
        return departures

    @staticmethod
    def _parse_db_time(value: str) -> Optional[datetime]:
        if len(value) != 10:
            return None
        return datetime.strptime(value, "%y%m%d%H%M").replace(tzinfo=BERLIN_TZ)

    @staticmethod
    def germanize_station_query(value: str) -> str:
        replacements = {
            "Koeln": "Köln",
            "koeln": "köln",
            "Duesseldorf": "Düsseldorf",
            "duesseldorf": "düsseldorf",
            "Muenster": "Münster",
            "muenster": "münster",
            "Muenchen": "München",
            "muenchen": "münchen",
        }
        result = value
        for plain, umlaut in replacements.items():
            result = result.replace(plain, umlaut)
        return result
