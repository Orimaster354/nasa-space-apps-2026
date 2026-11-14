"""Opt-in tests against the REAL IRSA archive (network, slow). Run with: pytest -m realdata

Target: WISE 0855 (coordinates from the challenge text). These check behaviour we have actually observed, not mock behaviour.
"""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from processing.irsa import CutoutCache
from services.core import Services
from store.registry import Registry

pytestmark = pytest.mark.realdata

WISE0855 = (133.795129, -7.245147)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("real")
    return TestClient(create_app(Services(Registry(d / "r.sqlite"), CutoutCache(d / "cutouts"), d / "images")))


def test_real_flow_resolve_list_image(client):
    obj = client.get("/api/v1/objects/resolve", params={"ra": WISE0855[0], "dec": WISE0855[1]}).json()
    page = client.get(f"/api/v1/objects/{obj['objectId']}/observations", params={"limit": 200}).json()
    assert page["total"] > 50                                    # 258 files were seen in the first probe
    bands = {o["bandpass"] for o in page["items"]}
    assert len(bands) >= 4
    times = [o["observedAt"] for o in page["items"]]
    assert times == sorted(times) and times[0].startswith("2025")

    # one frame from each of two different dates in the same band -> same grid
    d3 = [o for o in page["items"] if o["bandpass"] == "SPHEREx-D3"]
    assert len(d3) >= 2
    imgs = [client.get(f"/api/v1/observations/{o['observationId']}/image", params={"fovArcmin": 3}).json() for o in (d3[0], d3[-1])]
    assert all(i["availability"]["status"] == "available" for i in imgs), imgs
    assert imgs[0]["gridId"] == imgs[1]["gridId"]
    for i in imgs:
        png = client.get(i["imageUrl"])
        assert Image.open(io.BytesIO(png.content)).size == (i["width"], i["height"])

    obs = client.get(f"/api/v1/observations/{d3[0]['observationId']}").json()
    assert obs["qualitySummary"] in {"good", "degraded", "poor"} and obs["qualityFlags"]
