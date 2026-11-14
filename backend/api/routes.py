"""HTTP routes for contract v0.2.0 operations #1-#7 (objects, observations, images). No scientific logic here."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request

from services.core import NotFound, Services

from processing.imaging import DEFAULT_FOV_ARCMIN

from .schemas import (AstronomicalObject, ComparisonResult, CreateComparisonRequest, Observation, ObservationImage, Page,
                      PixelToSky)

router = APIRouter()
_EXCLUDE = {"response_model_exclude_none": True}


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


@router.get("/observations/{observation_id}", response_model=Observation, **_EXCLUDE)
def get_observation(request: Request, observation_id: str):
    return svc(request).get_observation(observation_id)


@router.get("/observations/{observation_id}/image", response_model=ObservationImage, **_EXCLUDE)
def get_observation_image(
    request: Request, observation_id: str,
    stretch: Literal["linear", "log", "sqrt", "asinh"] = "asinh",
    lowPercentile: float = Query(1.0, ge=0, le=100), highPercentile: float = Query(99.5, ge=0, le=100),
    centerRa: float | None = None, centerDec: float | None = None, fovArcmin: float = Query(DEFAULT_FOV_ARCMIN, gt=0),
):
    return svc(request).get_image(
        observation_id, stretch=stretch, low_pct=lowPercentile, high_pct=highPercentile,
        center_ra=centerRa, center_dec=centerDec, fov_arcmin=fovArcmin)


@router.get("/observations/{observation_id}/pixel-to-sky", response_model=PixelToSky, **_EXCLUDE)
def pixel_to_sky(
    request: Request, observation_id: str, x: float, y: float,
    centerRa: float | None = None, centerDec: float | None = None, fovArcmin: float = Query(DEFAULT_FOV_ARCMIN, gt=0),
):
    return svc(request).pixel_to_sky(observation_id, x, y, center_ra=centerRa, center_dec=centerDec, fov_arcmin=fovArcmin)


@router.post("/comparisons", response_model=ComparisonResult, **_EXCLUDE)
def create_comparison(request: Request, body: CreateComparisonRequest):
    return svc(request).create_comparison(body.baseObservationId, body.targetObservationId, body.mode)


@router.get("/comparisons/{comparison_id}", response_model=ComparisonResult, **_EXCLUDE)
def get_comparison(request: Request, comparison_id: str):
    return svc(request).get_comparison(comparison_id)
