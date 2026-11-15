"""Small SQLite registry mapping our opaque IDs to what they stand for (objects, observations).

IDs are opaque to clients (contract §1). Objects get an ID derived from their rounded position; observations get an ID derived
from the archive file URI, so both are stable across restarts and de-duplicated without extra state.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class Registry:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS objects (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS comparisons (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS measurements (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS discoveries (id TEXT PRIMARY KEY, body TEXT NOT NULL)")

    def _conn(self):
        return sqlite3.connect(self.path, timeout=30)

    def _put(self, table: str, key: str, body: dict) -> None:
        with self._lock, self._conn() as c:
            c.execute(f"INSERT INTO {table} (id, body) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET body = excluded.body",
                      (key, json.dumps(body)))

    def _get(self, table: str, key: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(f"SELECT body FROM {table} WHERE id = ?", (key,)).fetchone()
        return None if row is None else json.loads(row[0])

    def put_object(self, object_id: str, body: dict) -> None:
        self._put("objects", object_id, body)

    def get_object(self, object_id: str) -> dict | None:
        return self._get("objects", object_id)

    def put_observation(self, observation_id: str, body: dict) -> None:
        self._put("observations", observation_id, body)

    def get_observation(self, observation_id: str) -> dict | None:
        return self._get("observations", observation_id)

    def put_comparison(self, comparison_id: str, body: dict) -> None:
        self._put("comparisons", comparison_id, body)

    def get_comparison(self, comparison_id: str) -> dict | None:
        return self._get("comparisons", comparison_id)

    def put_measurement(self, measurement_id: str, body: dict) -> None:
        self._put("measurements", measurement_id, body)

    def get_measurement(self, measurement_id: str) -> dict | None:
        return self._get("measurements", measurement_id)

    def put_discovery(self, key: str, body: dict) -> None:
        self._put("discoveries", key, body)

    def get_discovery(self, key: str) -> dict | None:
        return self._get("discoveries", key)
