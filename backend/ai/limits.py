"""Protect the free LLM quota without making visitors feel it. Four guards, checked in this order:
  * cool-down: after the provider says "too many requests" (HTTP 429) we stop calling it for a short while;
  * per-minute cap for the whole site (free tiers allow only about 10 to 15 requests a minute): a request waits a few seconds for a
    free slot before giving up, so a brief rush is smoothed over instead of refused;
  * daily cap for the whole site;
  * per-visitor allowance.
When any guard says no, the companion keeps working with the friendly automated summary, so a busy judge never sees an error.
The numbers are configuration (SPX_CHAT_LIMIT_*): check the real limits for your model in Google AI Studio and set them there."""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone


class QuotaLimiter:
    def __init__(self, per_client: int = 60, global_daily: int = 1500, per_minute: int = 10, clock=time.time, client_ttl_s: float = 86400.0,
                 sleep=time.sleep):
        self.per_client, self.global_daily, self.per_minute = per_client, global_daily, per_minute
        self._clock, self._ttl, self._sleep = clock, client_ttl_s, sleep
        self._clients: dict[str, tuple[int, float]] = {}
        self._day, self._day_count = self._today(), 0
        self._recent: deque[float] = deque()
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%d")

    def cool_down(self, seconds: float) -> None:
        """The provider just told us to slow down: stop calling it for a while."""
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, self._clock() + seconds)

    def try_acquire(self, client_id: str, wait_s: float = 0.0) -> tuple[bool, str | None]:
        """Consume one AI message if allowed. Returns (allowed, friendly_reason_if_not). May wait up to `wait_s` for a per-minute slot."""
        deadline = self._clock() + wait_s
        while True:
            with self._lock:
                ok, reason, retry_in = self._check(client_id)
                if ok or retry_in is None or self._clock() + retry_in > deadline:
                    return ok, reason
            self._sleep(min(max(retry_in, 0.05), 1.0))

    def _check(self, client_id: str) -> tuple[bool, str | None, float | None]:
        """Returns (allowed, reason, retry_in). retry_in is set only for guards that clear on their own within a minute."""
        now = self._clock()
        if self._today() != self._day:
            self._day, self._day_count = self._today(), 0
        if now < self._cooldown_until:
            return False, "Navi's AI is catching its breath because a lot of explorers are asking at once. Try again in a minute.", self._cooldown_until - now
        while self._recent and now - self._recent[0] >= 60.0:
            self._recent.popleft()
        if len(self._recent) >= self.per_minute:
            return False, "Lots of explorers are asking at the same moment. Give it a few seconds and ask again.", 60.0 - (now - self._recent[0])
        if self._day_count >= self.global_daily:
            return False, "Navi has answered a lot of questions today and needs a rest.", None
        count, first = self._clients.get(client_id, (0, now))
        if now - first > self._ttl:
            count, first = 0, now
        if count >= self.per_client:
            return False, f"You have used your {self.per_client} free AI answers for now.", None
        self._clients[client_id] = (count + 1, first)
        self._day_count += 1
        self._recent.append(now)
        if len(self._clients) > 5000:                      # keep memory bounded
            for k in [k for k, (_, f) in self._clients.items() if now - f > self._ttl][:1000]:
                self._clients.pop(k, None)
        return True, None, None

    def remaining(self, client_id: str) -> int:
        with self._lock:
            count, first = self._clients.get(client_id, (0, self._clock()))
            if self._clock() - first > self._ttl:
                count = 0
            return max(0, self.per_client - count)
