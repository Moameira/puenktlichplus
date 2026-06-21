import json
import time
from pathlib import Path
from typing import Any, Optional


class JsonCache:
    def __init__(self, cache_dir: Path, ttl_seconds: int = 900) -> None:
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Optional[Any]:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - payload["created_at"] > self.ttl_seconds:
            return None
        return payload["value"]

    def set(self, key: str, value: Any) -> None:
        path = self.cache_dir / f"{key}.json"
        path.write_text(
            json.dumps({"created_at": time.time(), "value": value}, indent=2),
            encoding="utf-8",
        )
