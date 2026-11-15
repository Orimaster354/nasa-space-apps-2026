"""Saved investigations (contract operations 13-17), scoped to an anonymous visitor.

Contract OD-7 defaults to "no auth, single shared store". On a public site that would show every judge everyone else's saved work, so
each investigation is owned by an anonymous visitor key (the X-Client-Id header the page sends, else the caller's address) and a
visitor can only list, read, replace, delete or chat into their own. This is NOT security: anyone who knows another visitor's key
could use it. It is only there to keep casual visitors apart.

`messages` are server-managed: they change only through the chat endpoint and are ignored on create and update (contract).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from store.registry import Registry

MAX_PER_OWNER = 100
MAX_MESSAGES = 200


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    """Microsecond-resolution sort key, so items saved within the same second keep a stable newest-first order."""
    return datetime.now(timezone.utc).isoformat()


class TooMany(Exception):
    pass


class InvestigationStore:
    def __init__(self, registry: Registry):
        self.reg = registry

    @staticmethod
    def _summary(body: dict) -> dict:
        return {k: body[k] for k in ("investigationId", "title", "objectId", "updatedAt") if k in body}

    def list(self, owner: str, limit: int, offset: int) -> tuple[list[dict], int]:
        bodies = self.reg.inv_list(owner)                       # newest first
        return [self._summary(b) for b in bodies[offset:offset + limit]], len(bodies)

    def create(self, owner: str, data: dict) -> dict:
        if len(self.reg.inv_list(owner)) >= MAX_PER_OWNER:
            raise TooMany(f"You can keep up to {MAX_PER_OWNER} saved investigations; delete one first.")
        now = _now()
        body = {"investigationId": "inv_" + uuid.uuid4().hex[:10], "createdAt": now, "updatedAt": now, "messages": [], **data}
        self.reg.inv_put(body["investigationId"], owner, _stamp(), body)
        return body

    def get(self, owner: str, inv_id: str) -> dict | None:
        row = self.reg.inv_get(inv_id)
        return row[1] if row and row[0] == owner else None

    def update(self, owner: str, inv_id: str, data: dict) -> dict | None:
        cur = self.get(owner, inv_id)
        if cur is None:
            return None
        body = {"investigationId": inv_id, "createdAt": cur["createdAt"], "updatedAt": _now(), "messages": cur.get("messages", []), **data}
        self.reg.inv_put(inv_id, owner, _stamp(), body)
        return body

    def delete(self, owner: str, inv_id: str) -> bool:
        if self.get(owner, inv_id) is None:
            return False
        self.reg.inv_delete(inv_id)
        return True

    def append_messages(self, owner: str, inv_id: str, messages: list[dict]) -> bool:
        cur = self.get(owner, inv_id)
        if cur is None:
            return False
        cur["messages"] = (cur.get("messages", []) + messages)[-MAX_MESSAGES:]
        cur["updatedAt"] = _now()
        self.reg.inv_put(inv_id, owner, _stamp(), cur)
        return True
