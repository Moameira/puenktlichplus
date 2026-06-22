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

    async def next_departures(
        self,
        origin: str,
        destination: str,
        when: Optional[datetime] = None,
        limit: int = 2,
    ) -> dict:
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
            "departures": list(unique.values())[:limit],
        }

    async def connection_options(
        self,
        origin: str,
        destination: str,
        when: Optional[datetime] = None,
        max_options: int = 4,
    ) -> dict:
        moment = when or datetime.now(BERLIN_TZ)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=BERLIN_TZ)

        options = []
        direct = await self.next_departures(origin, destination, moment, limit=max_options)
        for departure in direct["departures"]:
            options.append(self._connection_from_departures([departure]))

        hubs = ["Düsseldorf Hbf", "Köln Hbf", "Duisburg Hbf", "Essen Hbf", "Dortmund Hbf"]
        normalized_origin = self._normalize_station(origin)
        normalized_destination = self._normalize_station(destination)
        for hub in hubs:
            if self._normalize_station(hub) in {normalized_origin, normalized_destination}:
                continue
            if len(options) >= max_options:
                break
            first_legs = await self.next_departures(origin, hub, moment, limit=2)
            for first_leg in first_legs["departures"]:
                if len(options) >= max_options:
                    break
                first_arrival = self.estimate_arrival(first_leg)
                second_start = first_arrival + timedelta(minutes=5)
                second_legs = await self.next_departures(hub, destination, second_start, limit=2)
                for second_leg in second_legs["departures"]:
                    if len(options) >= max_options:
                        break
                    if self._parse_iso(second_leg["scheduled_departure"]) <= first_arrival:
                        continue
                    options.append(self._connection_from_departures([first_leg, second_leg]))

        unique_options = {}
        for option in sorted(options, key=lambda item: item["departure"]):
            signature = tuple((leg["line"], leg["scheduled_departure"], leg["destination"]) for leg in option["legs"])
            unique_options.setdefault(signature, option)

        return {
            "origin": origin,
            "destination": destination,
            "searched_from": moment.isoformat(),
            "connections": list(unique_options.values())[:max_options],
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
    def parse_station_board_departures(xml_text: str, station: dict) -> List[dict]:
        if not xml_text.strip():
            return []
        root = ET.fromstring(xml_text)
        departures = []

        for stop in root.iter():
            if not stop.tag.endswith("s"):
                continue
            timeline = next((child for child in stop if child.tag.endswith("tl")), None)
            departure = next((child for child in stop if child.tag.endswith("dp")), None)
            if departure is None:
                continue

            planned_time = DbTimetablesClient._parse_db_time(departure.attrib.get("pt", ""))
            if planned_time is None:
                continue

            path = departure.attrib.get("ppth", "")
            path_stations = [item.strip() for item in path.split("|") if item.strip()]
            line = departure.attrib.get("l", "")
            train_number = timeline.attrib.get("n", "") if timeline is not None else ""
            train_category = timeline.attrib.get("c", "") if timeline is not None else ""
            train = " ".join(part for part in [train_category, train_number] if part).strip()
            departures.append(
                {
                    "origin": station["name"],
                    "destination": path_stations[-1] if path_stations else "",
                    "line": line or train or "DB",
                    "train": train or line or "DB",
                    "platform": departure.attrib.get("pp", ""),
                    "scheduled_departure": planned_time.isoformat(),
                    "path": path_stations,
                }
            )
        return departures

    def _connection_from_departures(self, departures: List[dict]) -> dict:
        legs = []
        for departure in departures:
            arrival = self.estimate_arrival(departure)
            legs.append(
                {
                    "origin": departure["origin"],
                    "destination": departure["destination"],
                    "line": departure["line"],
                    "scheduled_departure": departure["scheduled_departure"],
                    "scheduled_arrival": arrival.isoformat(),
                    "platform": departure.get("platform", ""),
                    "train": departure.get("train", departure["line"]),
                }
            )

        return {
            "kind": "direct" if len(legs) == 1 else "transfer",
            "departure": legs[0]["scheduled_departure"],
            "arrival": legs[-1]["scheduled_arrival"],
            "duration_minutes": round((self._parse_iso(legs[-1]["scheduled_arrival"]) - self._parse_iso(legs[0]["scheduled_departure"])).total_seconds() / 60),
            "transfer_count": max(0, len(legs) - 1),
            "legs": legs,
        }

    def estimate_arrival(self, departure: dict) -> datetime:
        departure_time = self._parse_iso(departure["scheduled_departure"])
        return departure_time + timedelta(minutes=self.estimate_duration_minutes(departure))

    @staticmethod
    def estimate_duration_minutes(departure: dict) -> int:
        route_durations = {
            ("koeln hbf", "duesseldorf hbf", "re1"): 35,
            ("köln hbf", "düsseldorf hbf", "re1"): 35,
            ("duesseldorf hbf", "duisburg hbf", "re1"): 18,
            ("düsseldorf hbf", "duisburg hbf", "re1"): 18,
            ("dortmund hbf", "essen hbf", "re6"): 24,
            ("essen hbf", "duesseldorf hbf", "re6"): 36,
            ("essen hbf", "düsseldorf hbf", "re6"): 36,
            ("bonn hbf", "koeln hbf", "re5"): 28,
            ("bonn hbf", "köln hbf", "re5"): 28,
        }
        key = (
            DbTimetablesClient._normalize_station(departure["origin"]),
            DbTimetablesClient._normalize_station(departure["destination"]),
            departure["line"].lower(),
        )
        if key in route_durations:
            return route_durations[key]
        path_stops = max(2, len(departure.get("path", [])) or 2)
        return min(180, max(20, round((path_stops - 1) * 3.5)))

    @staticmethod
    def _parse_db_time(value: str) -> Optional[datetime]:
        if len(value) != 10:
            return None
        return datetime.strptime(value, "%y%m%d%H%M").replace(tzinfo=BERLIN_TZ)

    @staticmethod
    def _parse_iso(value: str) -> datetime:
        return datetime.fromisoformat(value)

    @staticmethod
    def _normalize_station(value: str) -> str:
        return (
            value.lower()
            .replace("ö", "oe")
            .replace("ü", "ue")
            .replace("ä", "ae")
            .replace("ß", "ss")
        )

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
