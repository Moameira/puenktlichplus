import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from app.services.data_source import BERLIN_TZ, DATA_DIR, DbTimetablesClient


DEFAULT_DB_PATH = DATA_DIR / "collector.sqlite"


class TimetableSnapshotStore:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS station_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collected_at TEXT NOT NULL,
                    station_name TEXT NOT NULL,
                    station_eva TEXT NOT NULL,
                    line TEXT NOT NULL,
                    train TEXT NOT NULL,
                    platform TEXT,
                    scheduled_departure TEXT NOT NULL,
                    path TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_station_snapshot_unique
                ON station_snapshots(station_eva, train, scheduled_departure, collected_at)
                """
            )

    def save_departures(self, station: dict, departures: Iterable[dict], collected_at: datetime) -> int:
        rows = [
            (
                collected_at.isoformat(),
                station["name"],
                station["eva"],
                departure.get("line", ""),
                departure.get("train", ""),
                departure.get("platform", ""),
                departure["scheduled_departure"],
                "|".join(departure.get("path", [])),
            )
            for departure in departures
        ]
        if not rows:
            return 0

        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO station_snapshots (
                    collected_at,
                    station_name,
                    station_eva,
                    line,
                    train,
                    platform,
                    scheduled_departure,
                    path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            return connection.total_changes

    def summary(self) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS snapshot_count,
                    COUNT(DISTINCT station_eva) AS station_count,
                    MIN(collected_at) AS first_collected_at,
                    MAX(collected_at) AS last_collected_at
                FROM station_snapshots
                """
            ).fetchone()
        return dict(row)


class TimetableCollector:
    def __init__(self, client: DbTimetablesClient, store: TimetableSnapshotStore) -> None:
        self.client = client
        self.store = store

    async def collect_station(self, station_name: str, when: Optional[datetime] = None) -> dict:
        moment = when or datetime.now(BERLIN_TZ)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=BERLIN_TZ)

        station = await self.client._best_station(station_name)
        if not station:
            return {"station": station_name, "saved": 0, "error": "station not found"}

        board_xml = await self.client._station_plan(station["eva"], moment)
        departures = self.client.parse_station_board_departures(board_xml, station)
        saved = self.store.save_departures(station, departures, moment)
        return {"station": station["name"], "eva": station["eva"], "saved": saved}

    async def collect_many(self, station_names: Iterable[str]) -> dict:
        results = []
        for station_name in station_names:
            results.append(await self.collect_station(station_name))
        return {"stations": results, "summary": self.store.summary()}
