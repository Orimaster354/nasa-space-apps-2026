"""Durable copy of saved investigations in a Google Cloud Storage bucket (priority 4).

Cloud Run's disk is temporary: every redeploy or restart starts from the image, so visitors' saved investigations disappeared. This keeps a copy
of each one in a bucket and puts them back at start-up. It talks to the Cloud Storage JSON API directly (the service account's token comes from
the metadata server), so it adds no dependency, and it is OFF unless SPX_BACKUP_BUCKET is set (tests, local runs and the offline bundle never
touch the network).

Writes never slow or break a request: a change is queued (the newest change per investigation wins) and a worker uploads it, retrying until it
works. Reading the bucket at start-up is best effort: if it cannot be reached the app starts with what it has.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable
from urllib.parse import quote

import httpx

log = logging.getLogger("spx.backup")

METADATA_TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
API = "https://storage.googleapis.com"
RETRY_S = 5.0


class GcsBackup:
    def __init__(self, bucket: str, client: httpx.Client | None = None, prefix: str = "investigations/",
                 token_fn: Callable[[], str] | None = None, retry_s: float = RETRY_S):
        self.bucket, self.prefix, self.retry_s = bucket, prefix, retry_s
        self.client = client or httpx.Client(timeout=15.0)
        self._token_fn = token_fn
        self._tok: tuple[str, float] | None = None
        self._pending: dict[str, tuple | None] = {}                 # investigation id -> ("put", owner, updated, body) or ("delete",)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="investigation-backup", daemon=True)
        self._thread.start()

    # ---- auth ---------------------------------------------------------------------------------------------------
    def _token(self) -> str:
        if self._token_fn:
            return self._token_fn()
        if self._tok and time.monotonic() < self._tok[1] - 60:
            return self._tok[0]
        r = self.client.get(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
        r.raise_for_status()
        j = r.json()
        self._tok = (j["access_token"], time.monotonic() + float(j.get("expires_in", 300)))
        return self._tok[0]

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    def _name(self, inv_id: str) -> str:
        return f"{self.prefix}{inv_id}.json"

    # ---- API calls ----------------------------------------------------------------------------------------------
    def _upload(self, inv_id: str, owner: str, updated: str, body: dict) -> None:
        r = self.client.post(f"{API}/upload/storage/v1/b/{self.bucket}/o", params={"uploadType": "media", "name": self._name(inv_id)},
                             headers={**self._h(), "Content-Type": "application/json"},
                             content=json.dumps({"id": inv_id, "owner": owner, "updated": updated, "body": body}))
        r.raise_for_status()

    def _remove(self, inv_id: str) -> None:
        r = self.client.delete(f"{API}/storage/v1/b/{self.bucket}/o/{quote(self._name(inv_id), safe='')}", headers=self._h())
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    def _names(self) -> list[str]:
        out, token = [], None
        while True:
            params = {"prefix": self.prefix, "fields": "items(name),nextPageToken"}
            if token:
                params["pageToken"] = token
            r = self.client.get(f"{API}/storage/v1/b/{self.bucket}/o", params=params, headers=self._h())
            r.raise_for_status()
            j = r.json()
            out += [i["name"] for i in j.get("items", [])]
            token = j.get("nextPageToken")
            if not token:
                return out

    # ---- public --------------------------------------------------------------------------------------------------
    def changed(self, op: str, inv_id: str, owner: str = "", updated: str = "", body: dict | None = None) -> None:
        """Called by the registry after a save or delete. Never blocks and never raises."""
        with self._lock:
            self._pending[inv_id] = ("put", owner, updated, body) if op == "put" else ("delete",)
            self._idle.clear()
        self._wake.set()

    def restore_into(self, registry) -> int:
        """Put every investigation found in the bucket back into the registry (a newer local copy is kept). Best effort: returns how many were restored."""
        n = 0
        try:
            for name in self._names():
                r = self.client.get(f"{API}/storage/v1/b/{self.bucket}/o/{quote(name, safe='')}", params={"alt": "media"}, headers=self._h())
                r.raise_for_status()
                j = r.json()
                local = registry.inv_get(j["id"])
                if local is not None and local[1].get("updatedAt", "") >= j["body"].get("updatedAt", ""):
                    continue
                registry.inv_put(j["id"], j["owner"], j["updated"], j["body"], notify=False)
                n += 1
        except Exception as exc:                                    # the app must start even when the bucket cannot be read
            log.warning("could not restore saved investigations from gs://%s: %s", self.bucket, exc)
        log.info("restored %d saved investigation(s) from gs://%s", n, self.bucket)
        return n

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until every queued change has been uploaded (used by tests and at shutdown)."""
        return self._idle.wait(timeout)

    def close(self) -> None:
        self._stop = True
        self._wake.set()

    # ---- worker --------------------------------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop:
            self._wake.wait(timeout=self.retry_s)
            self._wake.clear()
            with self._lock:
                batch = dict(self._pending)
            for inv_id, op in batch.items():
                try:
                    if op[0] == "put":
                        self._upload(inv_id, op[1], op[2], op[3])
                    else:
                        self._remove(inv_id)
                except Exception as exc:
                    log.warning("backup of investigation %s failed (will retry): %s", inv_id, exc)
                    continue
                with self._lock:
                    if self._pending.get(inv_id) == op:               # unchanged since we started: done
                        del self._pending[inv_id]
            with self._lock:
                if not self._pending:
                    self._idle.set()
