"""Pydantic models mirroring the contract (docs/FRONTEND_BACKEND_CONTRACT.md §3, v0.2.0).

Optional fields are OMITTED when absent, never null: endpoints use `response_model_exclude_none=True`.
Only the models needed so far are defined; add the rest as endpoints are built.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class SkyCoordinate(_Model):
    raDeg: float
    decDeg: float


class Availability(_Model):
    status: Literal["available", "processing", "unavailable", "failed"]
    statusReason: str | None = None
    retryAfterSeconds: int | None = None


class QualityFlag(_Model):
    code: str
    severity: Literal["info", "warning", "critical"]
    description: str
    origin: Literal["source", "derived"]


class Resolver(_Model):
    name: str
    identifier: str | None = None


class ObservationSummary(_Model):
    count: int
    firstObservedAt: str | None = None
    lastObservedAt: str | None = None


class AstronomicalObject(_Model):
    objectId: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    coordinates: SkyCoordinate
    objectType: str | None = None
    resolver: Resolver
    observationSummary: ObservationSummary | None = None


class WavelengthRange(_Model):
    min: float
    max: float


class Observation(_Model):
    observationId: str
    objectIds: list[str] = Field(default_factory=list)
    irsaObservationId: str | None = None
    dataRelease: str | None = None
    observedAt: str | None = None
    center: SkyCoordinate
    footprint: list[SkyCoordinate] | None = None
    detector: str | None = None
    bandpass: str | None = None
    wavelengthRangeUm: WavelengthRange | None = None
    image: Availability
    qualityFlags: list[QualityFlag] = Field(default_factory=list)
    qualitySummary: Literal["good", "degraded", "poor", "unknown"] = "unknown"
    sourceMetadata: dict | None = None


class Page(_Model):
    items: list
    total: int
    limit: int
    offset: int
    coverageNote: str | None = None


class Stretch(_Model):
    mode: Literal["linear", "log", "sqrt", "asinh"]
    lowValue: float
    highValue: float
    unit: str | None = None


class WcsSummary(_Model):
    ctype1: str | None = None
    ctype2: str | None = None
    crval: tuple[float, float]
    crpix: tuple[float, float]
    cd: tuple[tuple[float, float], tuple[float, float]]
    pixelScaleArcsec: float | None = None
    approximate: bool


class ObservationImage(_Model):
    observationId: str
    availability: Availability
    imageUrl: str | None = None
    width: int | None = None
    height: int | None = None
    stretch: Stretch | None = None
    wcs: WcsSummary | None = None
    qualityMaskUrl: str | None = None
    fitsDownloadUrl: str | None = None
    gridId: str | None = None
    generatedAt: str


class PixelToSky(_Model):
    sky: SkyCoordinate
