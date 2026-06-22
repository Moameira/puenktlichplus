import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from app.services.cache import JsonCache
from app.services.collector import TimetableCollector, TimetableSnapshotStore
from app.services.data_source import DATA_DIR, DbTimetablesClient


DEFAULT_STATIONS = "Köln Hbf,Düsseldorf Hbf,Duisburg Hbf,Essen Hbf,Dortmund Hbf,Bonn Hbf"


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    station_names = [
        station.strip()
        for station in os.getenv("COLLECTOR_STATIONS", DEFAULT_STATIONS).split(",")
        if station.strip()
    ]
    client = DbTimetablesClient(JsonCache(Path(DATA_DIR) / "cache"))
    store = TimetableSnapshotStore()
    collector = TimetableCollector(client, store)
    result = await collector.collect_many(station_names)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
