"""Saved investigations are copied to a bucket and restored after a restart (priority 4). The bucket is a small in-memory stand-in for the
Cloud Storage JSON API behind httpx's mock transport; nothing here touches the network."""
import json
import threading
from urllib.parse import unquote

import httpx
import pytest

from store.backup import GcsBackup
from store.registry import Registry

pytestmark = pytest.mark.synthetic


class FakeGcs:
    def __init__(self):
        self.objects, self.fail, self.calls, self.tokens = {}, 0, [], 0
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        with self.lock:
            if "metadata.google.internal" in url:
                self.tokens += 1
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            assert request.headers.get("authorization") == "Bearer tok"
            self.calls.append((request.method, url.split("?")[0].split("storage.googleapis.com")[1]))
            if self.fail > 0:
                self.fail -= 1
                return httpx.Response(503)
            if request.method == "POST":
                name = request.url.params["name"]
                self.objects[name] = request.content.decode()
                return httpx.Response(200, json={"name": name})
            if request.method == "DELETE":
                self.objects.pop(unquote(url.split("/o/")[1].split("?")[0]), None)
                return httpx.Response(204)
            if "/o/" in url:
                return httpx.Response(200, content=self.objects[unquote(url.split("/o/")[1].split("?")[0])])
            prefix = request.url.params.get("prefix", "")
            return httpx.Response(200, json={"items": [{"name": n} for n in sorted(self.objects) if n.startswith(prefix)]})


def make(tmp_path, gcs, name="r.sqlite", **kw):
    reg = Registry(tmp_path / name)
    reg.backup = GcsBackup("b", client=httpx.Client(transport=httpx.MockTransport(gcs.handler)), retry_s=0.05, **kw)
    return reg


def body(inv_id="inv_1", title="Barnard", updated="2026-11-15T10:00:00Z"):
    return {"investigationId": inv_id, "title": title, "createdAt": updated, "updatedAt": updated, "notes": [{"text": "star moved"}], "messages": []}


def test_a_save_is_copied_to_the_bucket_and_survives_a_restart(tmp_path):
    gcs = FakeGcs()
    reg = make(tmp_path, gcs)
    reg.inv_put("inv_1", "c:visitor-a", "2026-11-15T10:00:00Z", body())
    assert reg.backup.flush(5)
    saved = json.loads(gcs.objects["investigations/inv_1.json"])
    assert saved["owner"] == "c:visitor-a" and saved["body"]["notes"][0]["text"] == "star moved"
    # the instance is replaced: a brand-new, empty registry restores from the bucket
    fresh = make(tmp_path, gcs, name="fresh.sqlite")
    assert fresh.inv_get("inv_1") is None
    assert fresh.backup.restore_into(fresh) == 1
    owner, got = fresh.inv_get("inv_1")
    assert owner == "c:visitor-a" and got["title"] == "Barnard"
    assert [i["investigationId"] for i in fresh.inv_list("c:visitor-a")] == ["inv_1"]
    assert fresh.inv_list("c:visitor-b") == []                                  # still private to its owner


def test_restoring_does_not_upload_the_same_data_back(tmp_path):
    gcs = FakeGcs()
    reg = make(tmp_path, gcs)
    reg.inv_put("inv_1", "c:a", "t", body())
    reg.backup.flush(5)
    before = [c for c in gcs.calls if c[0] == "POST"]
    fresh = make(tmp_path, gcs, name="fresh.sqlite")
    fresh.backup.restore_into(fresh)
    fresh.backup.flush(5)
    assert [c for c in gcs.calls if c[0] == "POST"] == before


def test_an_update_replaces_the_stored_copy_and_a_delete_removes_it(tmp_path):
    gcs = FakeGcs()
    reg = make(tmp_path, gcs)
    reg.inv_put("inv_1", "c:a", "t1", body(title="first"))
    reg.inv_put("inv_1", "c:a", "t2", body(title="second", updated="2026-11-15T11:00:00Z"))
    reg.backup.flush(5)
    assert json.loads(gcs.objects["investigations/inv_1.json"])["body"]["title"] == "second"
    reg.inv_delete("inv_1")
    assert reg.backup.flush(5) and "investigations/inv_1.json" not in gcs.objects


def test_a_newer_local_copy_is_not_overwritten_by_an_older_one_in_the_bucket(tmp_path):
    gcs = FakeGcs()
    old = make(tmp_path, gcs)
    old.inv_put("inv_1", "c:a", "t1", body(title="old"))
    old.backup.flush(5)
    reg = Registry(tmp_path / "other.sqlite")
    reg.backup = GcsBackup("b", client=httpx.Client(transport=httpx.MockTransport(gcs.handler)), retry_s=0.05)
    reg.inv_put("inv_1", "c:a", "t2", body(title="new", updated="2026-11-15T12:00:00Z"), notify=False)
    reg.backup.restore_into(reg)
    assert reg.inv_get("inv_1")[1]["title"] == "new"


def test_a_failing_bucket_never_breaks_a_save_and_the_upload_is_retried(tmp_path):
    gcs = FakeGcs()
    gcs.fail = 3                                                                 # the first three requests fail
    reg = make(tmp_path, gcs)
    reg.inv_put("inv_1", "c:a", "t", body())                                     # returns at once, does not raise
    assert reg.inv_get("inv_1") is not None
    assert reg.backup.flush(10)
    assert "investigations/inv_1.json" in gcs.objects                            # it got there once the bucket answered


def test_an_unreachable_bucket_at_start_up_means_the_app_starts_with_what_it_has(tmp_path):
    def down(request):
        raise httpx.ConnectError("no network")
    reg = Registry(tmp_path / "r.sqlite")
    reg.backup = GcsBackup("b", client=httpx.Client(transport=httpx.MockTransport(down)), token_fn=lambda: "tok", retry_s=0.05)
    assert reg.backup.restore_into(reg) == 0                                     # no exception


def test_the_token_is_fetched_once_and_reused(tmp_path):
    gcs = FakeGcs()
    reg = make(tmp_path, gcs)
    for i in range(3):
        reg.inv_put(f"inv_{i}", "c:a", "t", body(f"inv_{i}"))
    reg.backup.flush(5)
    assert gcs.tokens == 1


def test_it_is_off_without_a_bucket_name(tmp_path):
    reg = Registry(tmp_path / "r.sqlite")
    assert reg.backup is None
    reg.inv_put("inv_1", "c:a", "t", body())                                     # plain save, no network, no error
    from app.config import settings
    assert settings.backup_bucket == ""
