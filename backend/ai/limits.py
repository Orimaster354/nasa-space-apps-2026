"""Protect the free LLM quota. Two limits: a per-visitor message allowance and a global daily cap. When either is used up the
companion keeps working with the automated summary, so a busy judge never sees an error."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone


class QuotaLimiter:
    def __init__(self, per_client: int = 15, global_daily: int = 500, clock=time.time, client_ttl_s: float = 86400.0):
        self.per_client, self.global_daily, self._clock, self._ttl = per_client, global_daily, clock, client_ttl_s
        self._clients: dict[str, tuple[int, float]] = {}
        self._day, self._day_count = self._today(), 0
        self._lock = threading.Lock()

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%d")

    def try_acquire(self, client_id: str) -> tuple[bool, str | None]:
        """Consume one AI message if allowed. Returns (allowed, reason_if_not)."""
        with self._lock:
            now = self._clock()
            if self._today() != self._day:
                self._day, self._day_count = self._today(), 0
            if self._day_count >= self.global_daily:
                return False, "The daily AI message allowance for this site has been used up."
            count, first = self._clients.get(client_id, (0, now))
            if now - first > self._ttl:
                count, first = 0, now
            if count >= self.per_client:
                return False, f"You have used your {self.per_client} AI messages."
            self._clients[client_id] = (count + 1, first)
            self._day_count += 1
            if len(self._clients) > 5000:                      # keep memory bounded
                for k in [k for k, (_, f) in self._clients.items() if now - f > self._ttl][:1000]:
                    self._clients.pop(k, None)
            return True, None

    def remaining(self, client_id: str) -> int:
        with self._lock:
            count, first = self._clients.get(client_id, (0, self._clock()))
            if self._clock() - first > self._ttl:
                count = 0
            return max(0, self.per_client - count)
