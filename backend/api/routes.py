"""HTTP routes for contract v0.2.0 operations #1-#7 (objects, observations, images). No scientific logic here."""
from __future__ import annotations

import re
import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request, Response

from services.core import BadRequest, NotFound, Services
from services.investigations import TooMany

from processing.imaging import DEFAULT_FOV_ARCMIN

from .schemas import (AIResponse, SpectralData, Investigation, InvestigationInput, InvestigationSummary, AstronomicalObject, ComparisonResult, CreateComparisonRequest, CreateMeasurementRequest, Measurement,
                      Observation, ObservationImage, Page, PixelToSky, SendChatMessageRequest)

router = APIRouter()
_EXCLUDE = {"response_model_exclude_none": True}


_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def client_id(request: Request) -> str:
    """Anonymous visitor key for the AI message allowance: the X-Client-Id the page sends, else the caller's address."""
    cid = request.headers.get("x-client-id", "")
    if _CLIENT_ID.match(cid):
        return "c:" + cid
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return "ip:" + (fwd or (request.client.host if request.client else "unknown"))


def svc(request: Request) -> Services:
    return request.app.state.services


@router.get("/objects/search", response_model=Page, **_EXCLUDE)
def search_objects(request: Request, q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=50)):
    try:
        found = [svc(request).resolve(q=q)]
    except NotFound:
        return {"items": [], "total": 0, "limit": limit, "offset": 0, "coverageNote": f"No object found for '{q}'."}
    return {"items": found[:limit], "total": len(found), "limit": limit, "offset": 0}


@router.get("/objects/resolve", response_model=AstronomicalObject, **_EXCLUDE)
def resolve_object(request: Request, q: str | None = None, ra: float | None = None, dec: float | None = None):
    return svc(request).resolve(q=q, ra=ra, dec=dec)


@router.get("/objects/{object_id}", response_model=AstronomicalObject, **_EXCLUDE)
def get_object(request: Request, object_id: str):
    return svc(request).get_object(object_id)


@router.get("/objects/{object_id}/observations", response_model=Page, **_EXCLUDE)
def list_observations(
    request: Request, object_id: str,
    from_: str | None = Query(None, alias="from"), to: str | None = None,
    dataRelease: str | None = None, bandpass: str | None = None, minWavelengthUm: float | None = None, maxWavelengthUm: float | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    sort: Literal["observedAt", "-observedAt"] = "observedAt",
):
    return svc(request).list_observations(
        object_id, date_from=from_, date_to=to, data_release=dataRelease, bandpass=bandpass, min_wl=minWavelengthUm, max_wl=maxWavelengthUm,
        limit=limit, offset=offset, sort=sort)


@router.get("/objects/{object_id}/spectrum", response_model=SpectralData, **_EXCLUDE)
def get_spectrum(request: Request, object_id: str, ra: float | None = None, dec: float | None = None,
                 apertureRadiusArcsec: float = Query(12.0, ge=3.0, le=60.0), response: Response = None):
    result = svc(request).get_spectrum(object_id, ra=ra, dec=dec, radius=apertureRadiusArcsec)
    if result["availability"]["status"] == "processing":
        response.status_code = 202                    # contract: 202 + processing, the client polls the same GET
    return result


@router.get("/objects/{object_id}/candidates", response_model=Page, **_EXCLUDE)
def list_candidates(request: Request, object_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    """Candidate generation is not built yet. An honest empty page (never a made-up candidate) so the UI shows 'none' instead of an error."""
    svc(request).get_object(object_id)                      # 404 for an unknown object
    return {"items": [], "total": 0, "limit": limit, "offset": offset,
            "coverageNote": "Automated candidate detection is not available yet; compare the observations yourself."}


@router.get("/observations/{observation_id}", response_model=Observation, **_EXCLUDE)
def get_observation(request: Request, observation_id: str):
    return svc(request).get_observation(observation_id)


@router.get("/observations/{observation_id}/image", response_model=ObservationImage, **_EXCLUDE)
def get_observation_image(
    request: Request, observation_id: str,
    stretch: Literal["linear", "log", "sqrt", "asinh"] = "asinh",
    lowPercentile: float = Query(1.0, ge=0, le=100), highPercentile: float = Query(99.5, ge=0, le=100),
    centerRa: float | None = None, centerDec: float | None = None, fovArcmin: float = Query(DEFAULT_FOV_ARCMIN, gt=0),
    response: Response = None,
):
    result = svc(request).get_image(
        observation_id, stretch=stretch, low_pct=lowPercentile, high_pct=highPercentile,
        center_ra=centerRa, center_dec=centerDec, fov_arcmin=fovArcmin)
    if result["availability"]["status"] == "processing":
        response.status_code = 202                    # contract 5 "Async work": 202 + processing, the client polls the same GET
    return result


@router.get("/observations/{observation_id}/pixel-to-sky", response_model=PixelToSky, **_EXCLUDE)
def pixel_to_sky(
    request: Request, observation_id: str, x: float, y: float,
    centerRa: float | None = None, centerDec: float | None = None, fovArcmin: float = Query(DEFAULT_FOV_ARCMIN, gt=0),
):
    return svc(request).pixel_to_sky(observation_id, x, y, center_ra=centerRa, center_dec=centerDec, fov_arcmin=fovArcmin)


@router.post("/comparisons", response_model=ComparisonResult, **_EXCLUDE)
def create_comparison(request: Request, body: CreateComparisonRequest):
    return svc(request).create_comparison(body.baseObservationId, body.targetObservationId, body.mode)


@router.post("/measurements", response_model=Measurement, **_EXCLUDE)
def create_measurement(request: Request, body: CreateMeasurementRequest):
    return svc(request).create_measurement(body.kind, body.observationIds, body.region.model_dump() if body.region else None)


@router.post("/chat/messages", response_model=AIResponse, **_EXCLUDE)
def send_chat_message(request: Request, body: SendChatMessageRequest):
    ctx = body.context.model_dump(exclude_none=True)
    owner = client_id(request)
    store = svc(request).investigations
    if body.investigationId and store.get(owner, body.investigationId) is None:
        raise NotFound("Unknown investigation.")              # checked first so a bad id never spends an AI message
    reply = request.app.state.companion.respond(body.message, ctx, owner, body.investigationId)
    if body.investigationId:
        now = reply["message"]["createdAt"]
        user = {"messageId": "msg_" + uuid.uuid4().hex[:10], "role": "user", "content": body.message, "createdAt": now, "context": ctx}
        store.append_messages(owner, body.investigationId, [user, reply["message"]])
    return reply


@router.get("/investigations", response_model=Page, **_EXCLUDE)
def list_investigations(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    items, total = svc(request).investigations.list(client_id(request), limit, offset)
    return {"items": [InvestigationSummary(**i).model_dump(exclude_none=True) for i in items], "total": total, "limit": limit, "offset": offset}


@router.post("/investigations", response_model=Investigation, status_code=201, **_EXCLUDE)
def create_investigation(request: Request, body: InvestigationInput):
    try:
        return svc(request).investigations.create(client_id(request), body.model_dump(exclude_none=True))
    except TooMany as exc:
        raise BadRequest(str(exc)) from exc


@router.get("/investigations/{investigation_id}", response_model=Investigation, **_EXCLUDE)
def get_investigation(request: Request, investigation_id: str):
    inv = svc(request).investigations.get(client_id(request), investigation_id)
    if inv is None:
        raise NotFound("Unknown investigation.")
    return inv


@router.put("/investigations/{investigation_id}", response_model=Investigation, **_EXCLUDE)
def update_investigation(request: Request, investigation_id: str, body: InvestigationInput):
    inv = svc(request).investigations.update(client_id(request), investigation_id, body.model_dump(exclude_none=True))
    if inv is None:
        raise NotFound("Unknown investigation.")
    return inv


@router.delete("/investigations/{investigation_id}", status_code=204)
def delete_investigation(request: Request, investigation_id: str):
    if not svc(request).investigations.delete(client_id(request), investigation_id):
        raise NotFound("Unknown investigation.")
    return Response(status_code=204)


@router.get("/measurements/{measurement_id}", response_model=Measurement, **_EXCLUDE)
def get_measurement(request: Request, measurement_id: str):
    return svc(request).get_measurement(measurement_id)


@router.get("/comparisons/{comparison_id}", response_model=ComparisonResult, **_EXCLUDE)
def get_comparison(request: Request, comparison_id: str):
    return svc(request).get_comparison(comparison_id)
