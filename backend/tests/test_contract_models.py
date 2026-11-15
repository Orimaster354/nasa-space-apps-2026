"""P0-09 and P0-34: every contract type has a model; the examples in docs/DATA_MODELS.md validate against them; and every implemented
operation returns a body that validates against its model, with no nulls (optional fields are omitted, never null)."""
import json
import re
from pathlib import Path

import pytest

from api import schemas
from tests.test_api import STAR, _no_nulls, _object, _observations, env                # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic

DOC = Path(__file__).resolve().parents[2] / "docs" / "DATA_MODELS.md"
ORDER = ["AstronomicalObject", "Observation", "ObservationImage", "SpectralData", "Measurement", "ComparisonResult", "Investigation",
         "ChatMessage", "AIResponse", "Candidate", "ProperMotion"]                           # sections 1-11 of DATA_MODELS.md


def _examples() -> list[dict]:
    return [json.loads(b) for b in re.findall(r"```json\n(.*?)```", DOC.read_text(encoding="utf-8"), flags=re.S)]


def _contains(whole, part, path="$"):
    """Every key and value in `part` is present in `whole` (the dump may add defaulted lists; it must never drop or change a value)."""
    if isinstance(part, dict):
        assert isinstance(whole, dict), path
        for k, v in part.items():
            assert k in whole, f"{path}.{k} was dropped"
            _contains(whole[k], v, f"{path}.{k}")
    elif isinstance(part, list):
        assert isinstance(whole, list) and len(whole) == len(part), path
        for i, (w, p) in enumerate(zip(whole, part)):
            _contains(w, p, f"{path}[{i}]")
    else:
        assert whole == part, f"{path}: {whole!r} != {part!r}"


def test_there_is_one_example_per_documented_model():
    assert len(_examples()) == len(ORDER) == 11


@pytest.mark.parametrize("index, name", list(enumerate(ORDER)))
def test_each_documented_example_validates_and_round_trips(index, name):
    example = _examples()[index]
    model = getattr(schemas, name).model_validate(example)
    dumped = model.model_dump(mode="json", exclude_none=True)
    _no_nulls(dumped)
    _contains(dumped, example)


def test_models_exist_for_every_contract_type_and_envelope():
    for name in ORDER + ["SkyCoordinate", "Availability", "QualityFlag", "WcsSummary", "Stretch", "SpectralPoint", "Note", "Bookmark",
                         "InvestigationEvent", "InvestigationInput", "ChatContext", "AIReference", "SuggestedAction", "Page", "ApiError",
                         "CreateComparisonRequest", "CreateMeasurementRequest", "SendChatMessageRequest", "Compatibility", "ProperMotion"]:
        assert hasattr(schemas, name), name


def test_candidate_enforces_the_honesty_rules():
    base = _examples()[9]
    for bad in ({"isProbability": True}, {"status": "confirmed"}, {"score": 1.5}, {"kind": "discovery"}):
        with pytest.raises(Exception):
            schemas.Candidate.model_validate({**base, **bad})


def test_api_error_envelope_accepts_only_contract_codes():
    schemas.ApiError.model_validate({"error": {"code": "DATA_UNAVAILABLE", "message": "x", "retryable": False, "requestId": "abc"}})
    with pytest.raises(Exception):
        schemas.ApiError.model_validate({"error": {"code": "SOMETHING_ELSE", "message": "x", "retryable": False}})


# ---- one test per implemented operation (contract section 5) ------------------------------------------------------------------
def _ok(r, status=200):
    assert r.status_code == status, r.text
    return r.json()


def test_op01_search_objects(env):
    c, *_ = env
    page = _ok(c.get("/api/v1/objects/search", params={"q": "Barnard's star"}))
    schemas.Page.model_validate(page)
    [schemas.AstronomicalObject.model_validate(i) for i in page["items"]]
    _no_nulls(page)


def test_op02_and_op03_resolve_and_get_object(env):
    c, *_ = env
    o = _ok(c.get("/api/v1/objects/resolve", params={"q": "Barnard's star"}))
    schemas.AstronomicalObject.model_validate(o)
    assert schemas.AstronomicalObject.model_validate(o).properMotion.provenanceKind == "catalog"
    _no_nulls(o)
    assert _ok(c.get(f"/api/v1/objects/{o['objectId']}")) == o


def test_op04_and_op05_observations(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    page = _ok(c.get(f"/api/v1/objects/{oid}/observations"))
    schemas.Page.model_validate(page)
    obs = [schemas.Observation.model_validate(i) for i in page["items"]]
    _no_nulls(page)
    one = _ok(c.get(f"/api/v1/observations/{obs[0].observationId}"))
    schemas.Observation.model_validate(one)
    _no_nulls(one)


def test_op06_observation_image(env):
    c, *_ = env
    _, obs = _observations(c)
    img = _ok(c.get(f"/api/v1/observations/{obs[0]['observationId']}/image"))
    m = schemas.ObservationImage.model_validate(img)
    assert m.availability.status == "available" and m.imageUrl and m.width and m.wcs and m.gridId
    _no_nulls(img)


def test_op07_pixel_to_sky(env):
    c, *_ = env
    _, obs = _observations(c)
    r = _ok(c.get(f"/api/v1/observations/{obs[0]['observationId']}/pixel-to-sky", params={"x": 10, "y": 10}))
    schemas.PixelToSky.model_validate(r)
    _no_nulls(r)


def test_op08_and_op09_comparisons(env):
    c, *_ = env
    _, obs = _observations(c)
    body = {"baseObservationId": obs[0]["observationId"], "targetObservationId": obs[1]["observationId"], "mode": "difference"}
    created = _ok(c.post("/api/v1/comparisons", json=body))
    m = schemas.ComparisonResult.model_validate(created)
    assert m.differenceImage is not None and {k.name for k in m.compatibility.checks} >= {"wavelength", "alignment", "calibration"}
    _no_nulls(created)
    assert _ok(c.get(f"/api/v1/comparisons/{created['comparisonId']}")) == created


def test_op10_spectrum_is_not_implemented_yet(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    assert c.get(f"/api/v1/objects/{oid}/spectrum").status_code in (404, 405)       # documented gap: the model exists, the endpoint does not


def test_op11_measurement(env):
    c, *_ = env
    _, obs = _observations(c)
    region = {"center": {"raDeg": STAR[0], "decDeg": STAR[1]}, "radiusArcsec": 20}
    one = _ok(c.post("/api/v1/measurements", json={"kind": "aperture_flux", "observationIds": [obs[0]["observationId"]], "region": region}))
    schemas.Measurement.model_validate(one)
    _no_nulls(one)
    two = _ok(c.post("/api/v1/measurements", json={"kind": "flux_change", "observationIds": [obs[0]["observationId"], obs[1]["observationId"]], "region": region}))
    schemas.Measurement.model_validate(two)
    _no_nulls(two)


def test_op12_chat(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    r = _ok(c.post("/api/v1/chat/messages", json={"message": "hello", "context": {"objectId": oid}}))
    m = schemas.AIResponse.model_validate(r)
    assert m.message.role == "assistant" and m.caveats and m.availability.status == "available"
    _no_nulls(r)


def test_op13_to_op17_investigations(env):
    c, *_ = env
    h = {"X-Client-Id": "contract-test-1"}
    created = _ok(c.post("/api/v1/investigations", json={"title": "t"}, headers=h), 201)
    schemas.Investigation.model_validate(created)
    _no_nulls(created)
    page = _ok(c.get("/api/v1/investigations", headers=h))
    schemas.Page.model_validate(page)
    [schemas.InvestigationSummary.model_validate(i) for i in page["items"]]
    iid = created["investigationId"]
    schemas.Investigation.model_validate(_ok(c.get(f"/api/v1/investigations/{iid}", headers=h)))
    schemas.Investigation.model_validate(_ok(c.put(f"/api/v1/investigations/{iid}", json={"title": "t2"}, headers=h)))
    assert c.delete(f"/api/v1/investigations/{iid}", headers=h).status_code == 204


def test_op18_health(env):
    c, *_ = env
    assert _ok(c.get("/api/v1/health")) == {"status": "ok", "contractVersion": "0.2.0"}


def test_op19_candidates(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    page = _ok(c.get(f"/api/v1/objects/{oid}/candidates"))
    schemas.Page.model_validate(page)
    assert page["items"] == [] and page["coverageNote"]                               # honest empty page until detection exists


def test_every_error_response_matches_the_envelope(env):
    c, *_ = env
    for r in (c.get("/api/v1/objects/obj_nope"), c.get("/api/v1/objects/resolve"), c.get("/api/v1/observations/obs_nope/image"),
              c.post("/api/v1/measurements", json={"kind": "nope", "observationIds": []})):
        assert r.status_code in (400, 404, 422)
        schemas.ApiError.model_validate(r.json())
