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
            c.execute("CREATE TABLE IF NOT EXISTS spectra (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY, object_id TEXT NOT NULL, body TEXT NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS candidates_object ON candidates (object_id)")
            c.execute("CREATE TABLE IF NOT EXISTS candidate_runs (object_id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS investigations (id TEXT PRIMARY KEY, owner TEXT NOT NULL, updated TEXT NOT NULL, body TEXT NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS investigations_owner ON investigations (owner, updated)")

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

    def put_candidates(self, object_id: str, candidates: list[dict], run: dict) -> None:
        """Replace an object's candidates and record that the search ran (even when it found none), in one transaction."""
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM candidates WHERE object_id = ?", (object_id,))
            c.executemany("INSERT INTO candidates (id, object_id, body) VALUES (?, ?, ?)", [(x["candidateId"], object_id, json.dumps(x)) for x in candidates])
            c.execute("INSERT INTO candidate_runs (object_id, body) VALUES (?, ?) ON CONFLICT(object_id) DO UPDATE SET body = excluded.body",
                      (object_id, json.dumps(run)))

    def get_candidate(self, candidate_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT body FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def list_candidates(self, object_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT body FROM candidates WHERE object_id = ?", (object_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get_candidate_run(self, object_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT body FROM candidate_runs WHERE object_id = ?", (object_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def put_observations(self, items: list[tuple[str, dict]]) -> None:
        """Store many observation records in ONE transaction (listing a target with thousands of files must not open a connection per file)."""
        if not items:
            return
        with self._lock, self._conn() as c:
            c.executemany("INSERT INTO observations (id, body) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET body = excluded.body",
                          [(k, json.dumps(v)) for k, v in items])

    def get_observations(self, ids: list[str]) -> dict[str, dict]:
        """Read many observation records with a few queries (SQLite limits the number of parameters, so in chunks)."""
        out: dict[str, dict] = {}
        with self._conn() as c:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                rows = c.execute(f"SELECT id, body FROM observations WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
                out.update({r[0]: json.loads(r[1]) for r in rows})
        return out

    def put_spectrum(self, spectrum_id: str, body: dict) -> None:
        self._put("spectra", spectrum_id, body)

    def get_spectrum(self, spectrum_id: str) -> dict | None:
        return self._get("spectra", spectrum_id)

    def put_discovery(self, key: str, body: dict) -> None:
        self._put("discoveries", key, body)

    def get_discovery(self, key: str) -> dict | None:
        return self._get("discoveries", key)

    # ---- investigations (owner-scoped; see services/investigations.py)
    def inv_put(self, inv_id: str, owner: str, updated: str, body: dict) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO investigations (id, owner, updated, body) VALUES (?, ?, ?, ?) "
                      "ON CONFLICT(id) DO UPDATE SET owner = excluded.owner, updated = excluded.updated, body = excluded.body",
                      (inv_id, owner, updated, json.dumps(body)))

    def inv_get(self, inv_id: str) -> tuple[str, dict] | None:
        with self._conn() as c:
            row = c.execute("SELECT owner, body FROM investigations WHERE id = ?", (inv_id,)).fetchone()
        return None if row is None else (row[0], json.loads(row[1]))

    def inv_list(self, owner: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT body FROM investigations WHERE owner = ? ORDER BY updated DESC, id", (owner,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def inv_delete(self, inv_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM investigations WHERE id = ?", (inv_id,))
