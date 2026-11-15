"""Saved investigations (contract 13-17): the UI's real payload, per-visitor separation, server-managed messages, restarts."""
import pytest

from ai.companion import Companion
from ai.limits import QuotaLimiter
from ai.llm import ProviderChain
from store.registry import Registry
from tests.test_api import _no_nulls, _object, env                                 # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic

A = {"X-Client-Id": "visitor-aaaaaaaa"}
B = {"X-Client-Id": "visitor-bbbbbbbb"}
URL = "/api/v1/investigations"


def payload(title="Barnard's star, first look", object_id="obj_1"):
    """Shaped like what frontend/js/views/notes.js sends: { title, objectId?, notes, bookmarks, history }."""
    return {
        "title": title, "objectId": object_id,
        "notes": [{"noteId": "n1", "text": "Star sits left of centre.", "createdAt": "2026-11-15T10:00:00Z", "observationId": "obs_1"}],
        "bookmarks": [{"bookmarkId": "b1", "targetType": "observation", "targetId": "obs_1", "label": "Aug frame", "createdAt": "2026-11-15T10:01:00Z"}],
        "history": [{"eventId": "e1", "type": "object_opened", "targetId": "obj_1", "at": "2026-11-15T09:59:00Z"},
                    {"eventId": "e2", "type": "some_future_event", "at": "2026-11-15T10:02:00Z"}],      # an event type the contract does not list
    }


def test_create_get_list_update_delete_round_trip(env):
    c, *_ = env
    r = c.post(URL, json=payload(), headers=A)
    assert r.status_code == 201
    inv = r.json()
    _no_nulls(inv)
    assert inv["investigationId"].startswith("inv_") and inv["messages"] == [] and inv["createdAt"] == inv["updatedAt"]
    assert inv["notes"][0]["text"] == "Star sits left of centre." and inv["history"][1]["type"] == "some_future_event"
    iid = inv["investigationId"]
    assert c.get(f"{URL}/{iid}", headers=A).json() == inv
    page = c.get(URL, headers=A).json()
    assert page["total"] == 1 and page["items"] == [{"investigationId": iid, "title": inv["title"], "objectId": "obj_1", "updatedAt": inv["updatedAt"]}]

    upd = c.put(f"{URL}/{iid}", json={**payload(title="Renamed"), "notes": []}, headers=A).json()
    assert upd["title"] == "Renamed" and upd["notes"] == [] and upd["createdAt"] == inv["createdAt"] and upd["investigationId"] == iid
    assert c.get(f"{URL}/{iid}", headers=A).json()["title"] == "Renamed"

    d = c.delete(f"{URL}/{iid}", headers=A)
    assert d.status_code == 204 and d.content == b""
    assert c.get(f"{URL}/{iid}", headers=A).status_code == 404
    assert c.delete(f"{URL}/{iid}", headers=A).status_code == 404
    assert c.get(URL, headers=A).json()["items"] == []


def test_visitors_only_see_their_own(env):
    c, *_ = env
    iid = c.post(URL, json=payload(), headers=A).json()["investigationId"]
    assert c.get(URL, headers=B).json()["total"] == 0
    for call in (lambda: c.get(f"{URL}/{iid}", headers=B), lambda: c.put(f"{URL}/{iid}", json=payload(), headers=B),
                 lambda: c.delete(f"{URL}/{iid}", headers=B)):
        r = call()
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert c.get(f"{URL}/{iid}", headers=A).status_code == 200                       # untouched by the other visitor's attempts


def test_list_is_newest_first_and_paged(env):
    c, *_ = env
    ids = [c.post(URL, json=payload(title=f"t{i}"), headers=A).json()["investigationId"] for i in range(3)]
    c.put(f"{URL}/{ids[0]}", json=payload(title="t0 edited"), headers=A)             # most recently updated now
    page = c.get(URL, params={"limit": 2}, headers=A).json()
    assert page["total"] == 3 and [i["title"] for i in page["items"]] == ["t0 edited", "t2"]
    assert [i["title"] for i in c.get(URL, params={"limit": 2, "offset": 2}, headers=A).json()["items"]] == ["t1"]


def test_validation(env):
    c, *_ = env
    for bad in ({**payload(), "title": ""}, {**payload(), "title": "x" * 201}, {"notes": []},
                {**payload(), "notes": [{"noteId": "n", "text": "x" * 5001, "createdAt": "t"}]},
                {**payload(), "notes": [{"noteId": "n", "createdAt": "t"}]}):
        r = c.post(URL, json=bad, headers=A)
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"
    assert c.post(URL, json={"title": "minimal"}, headers=A).status_code == 201     # notes, bookmarks, history default to empty


def test_a_visitor_cannot_hoard_unlimited_investigations(env):
    c, *_ = env
    from services.investigations import MAX_PER_OWNER
    for i in range(MAX_PER_OWNER):
        c.app.state.services.investigations.create("c:" + A["X-Client-Id"], {"title": f"t{i}"})
    r = c.post(URL, json={"title": "one too many"}, headers=A)
    assert r.status_code == 400 and "delete one first" in r.json()["error"]["message"]
    assert c.post(URL, json={"title": "other visitor"}, headers=B).status_code == 201


def test_messages_are_server_managed_and_ignored_on_create_and_update(env):
    c, *_ = env
    fake = {"messageId": "m1", "role": "user", "content": "forged", "createdAt": "2026-11-15T10:00:00Z"}
    inv = c.post(URL, json={**payload(), "messages": [fake]}, headers=A).json()
    assert inv["messages"] == []
    c.app.state.services.investigations.append_messages("c:" + A["X-Client-Id"], inv["investigationId"], [fake])
    upd = c.put(f"{URL}/{inv['investigationId']}", json={**payload(), "messages": []}, headers=A).json()
    assert upd["messages"] == [fake]                                                  # a PUT cannot erase or forge the conversation


def test_chat_appends_both_messages_to_the_investigation(env):
    c, *_ = env
    c.app.state.companion = Companion(c.app.state.services, ProviderChain([]), QuotaLimiter())
    oid = _object(c)["objectId"]
    iid = c.post(URL, json={"title": "chatty", "objectId": oid}, headers=A).json()["investigationId"]
    r = c.post("/api/v1/chat/messages", json={"investigationId": iid, "message": "what is this?", "context": {"objectId": oid}}, headers=A)
    assert r.status_code == 200
    msgs = c.get(f"{URL}/{iid}", headers=A).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[0]["content"] == "what is this?"
    assert msgs[0]["context"] == {"objectId": oid} and msgs[1] == r.json()["message"]
    c.post("/api/v1/chat/messages", json={"investigationId": iid, "message": "and again", "context": {}}, headers=A)
    assert len(c.get(f"{URL}/{iid}", headers=A).json()["messages"]) == 4


def test_chat_with_someone_elses_or_an_unknown_investigation_is_a_404_and_costs_nothing(env):
    c, *_ = env
    comp = Companion(c.app.state.services, ProviderChain([]), QuotaLimiter(per_client=1))
    c.app.state.companion = comp
    iid = c.post(URL, json={"title": "mine"}, headers=A).json()["investigationId"]
    for who, inv in ((B, iid), (A, "inv_doesnotexist")):
        r = c.post("/api/v1/chat/messages", json={"investigationId": inv, "message": "hi", "context": {}}, headers=who)
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert c.get(f"{URL}/{iid}", headers=A).json()["messages"] == []


def test_investigations_survive_a_restart(env, tmp_path):
    c, *_ = env
    iid = c.post(URL, json=payload(), headers=A).json()["investigationId"]
    reopened = Registry(tmp_path / "r.sqlite")                                        # same file the app's registry uses
    from services.investigations import InvestigationStore
    again = InvestigationStore(reopened).get("c:" + A["X-Client-Id"], iid)
    assert again is not None and again["title"] == "Barnard's star, first look" and again["notes"][0]["noteId"] == "n1"
