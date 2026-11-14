import pytest
from fastapi.testclient import TestClient

from app.main import create_app

pytestmark = pytest.mark.synthetic


def test_health_reports_contract_version():
    r = TestClient(create_app()).get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "contractVersion": "0.2.0"}


def test_unknown_path_uses_error_envelope():
    r = TestClient(create_app()).get("/api/v1/nope")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "NOT_FOUND" and err["retryable"] is False and err["requestId"]
