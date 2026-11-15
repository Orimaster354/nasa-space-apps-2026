"""Pydantic models mirroring the contract (docs/FRONTEND_BACKEND_CONTRACT.md §3, v0.2.0).

Optional fields are OMITTED when absent, never null: endpoints use `response_model_exclude_none=True`.
Every contract type has a model (P0-09). Spectrum and candidate models exist before their endpoints do.
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


class ProperMotion(_Model):
    value: float
    unit: str
    source: str
    provenanceKind: Literal["catalog", "derived_from_observations", "unvalidated_displacement"]
    catalogName: str | None = None
    catalogId: str | None = None
    pmRaMasPerYr: float | None = None
    pmDecMasPerYr: float | None = None
    convention: str | None = None
    referenceEpoch: str | None = None
    uncertainty: float | None = None


class AstronomicalObject(_Model):
    objectId: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    coordinates: SkyCoordinate
    objectType: str | None = None
    properMotion: ProperMotion | None = None
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
    wavelengthAtObjectUm: float | None = None     # additive, optional (pending contract addition): where the object falls in the band
    bandwidthAtObjectUm: float | None = None


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


class CompatibilityCheck(_Model):
    name: Literal["wavelength", "calibration", "alignment", "quality", "dataRelease", "footprint", "background"]
    result: Literal["pass", "warn", "fail", "notEvaluated"]
    detail: str


class Compatibility(_Model):
    verdict: Literal["comparable", "comparable_with_caveats", "not_comparable", "unknown"]
    checks: list[CompatibilityCheck] = Field(default_factory=list)


class Alignment(_Model):
    method: str
    residualArcsec: float | None = None


class Region(_Model):
    center: SkyCoordinate
    radiusArcsec: float = Field(gt=0, le=120)


class CreateMeasurementRequest(_Model):
    kind: Literal["aperture_flux", "flux_change", "angular_separation", "region_statistics", "position_centroid", "derived_motion"]
    observationIds: list[str]
    region: Region | None = None


class Measurement(_Model):
    measurementId: str
    kind: Literal["aperture_flux", "flux_change", "angular_separation", "region_statistics", "position_centroid", "derived_motion"]
    value: float
    unit: str
    uncertainty: float | None = None
    uncertaintyBasis: str | None = None
    signalToNoise: float | None = None          # additive, optional (pending contract addition): value / uncertainty
    wavelengthUm: float | None = None           # additive, optional (pending contract addition): wavelength at the region centre
    bandwidthUm: float | None = None
    observationIds: list[str]
    region: Region | None = None
    method: str
    qualityFlags: list[QualityFlag] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    createdAt: str


class Selection(_Model):
    observationId: str
    sky: SkyCoordinate | None = None
    radiusArcsec: float | None = None


class ChatContext(_Model):
    activeView: Literal["viewer", "timeline", "comparison", "spectrum", "workspace"] | None = None
    candidateId: str | None = None
    mode: Literal["beginner", "advanced"] | None = None
    objectId: str | None = None
    observationIds: list[str] | None = None
    comparisonId: str | None = None
    spectrumId: str | None = None
    measurementIds: list[str] | None = None
    selection: Selection | None = None


class SendChatMessageRequest(_Model):
    investigationId: str | None = None
    message: str = Field(min_length=1, max_length=2000)
    context: ChatContext = Field(default_factory=ChatContext)


class ChatMessage(_Model):
    messageId: str
    role: Literal["user", "assistant"]
    content: str
    createdAt: str
    context: ChatContext | None = None


class AIReference(_Model):
    targetType: Literal["observation", "comparison", "spectrum", "measurement", "object", "candidate"]
    targetId: str


class SuggestedAction(_Model):
    kind: Literal["open_observation", "open_comparison", "open_spectrum", "add_note", "run_comparison"]
    label: str
    targetId: str | None = None
    params: dict[str, str | float] | None = None


class AIResponse(_Model):
    message: ChatMessage
    references: list[AIReference]
    caveats: list[str]
    suggestedActions: list[SuggestedAction]
    availability: Availability


class Note(_Model):
    noteId: str
    text: str = Field(max_length=5000)
    createdAt: str
    observationId: str | None = None
    comparisonId: str | None = None


class Bookmark(_Model):
    bookmarkId: str
    targetType: str = Field(max_length=40)          # contract lists six values; any short string is accepted so a UI change cannot break Save
    targetId: str
    label: str | None = Field(None, max_length=200)
    createdAt: str


class InvestigationEvent(_Model):
    eventId: str
    type: str = Field(max_length=40)                # contract lists five values; see Bookmark.targetType
    targetId: str | None = None
    at: str


class InvestigationInput(_Model):
    title: str = Field(min_length=1, max_length=200)
    objectId: str | None = None
    notes: list[Note] = Field(default_factory=list, max_length=200)
    bookmarks: list[Bookmark] = Field(default_factory=list, max_length=200)
    history: list[InvestigationEvent] = Field(default_factory=list, max_length=500)


class Investigation(_Model):
    investigationId: str
    title: str
    objectId: str | None = None
    createdAt: str
    updatedAt: str
    notes: list[Note] = Field(default_factory=list)
    bookmarks: list[Bookmark] = Field(default_factory=list)
    history: list[InvestigationEvent] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)


class InvestigationSummary(_Model):
    investigationId: str
    title: str
    objectId: str | None = None
    updatedAt: str


class SpectralPoint(_Model):
    wavelengthUm: float
    wavelengthMinUm: float | None = None
    wavelengthMaxUm: float | None = None
    bandIndex: int | None = None
    available: bool
    unavailableReason: str | None = None
    flux: float | None = None
    fluxUncertainty: float | None = None
    qualityFlags: list[QualityFlag] = Field(default_factory=list)
    observationId: str | None = None
    observedAt: str | None = None


class SpectralData(_Model):
    spectrumId: str
    objectId: str
    availability: Availability
    position: SkyCoordinate
    apertureRadiusArcsec: float | None = None
    extractionMethod: str
    wavelengthUnit: Literal["um"]
    fluxUnit: str
    uncertaintyBasis: str | None = None
    points: list[SpectralPoint] = Field(default_factory=list)
    expectedBandCount: int | None = None
    warnings: list[str] = Field(default_factory=list)
    generatedAt: str


class PixelXY(_Model):
    x: float
    y: float


class TrackPoint(_Model):
    observationId: str
    sky: SkyCoordinate
    pixel: PixelXY | None = None


class CandidateMotion(_Model):
    value: float
    unit: str
    uncertainty: float | None = None
    method: str


class Candidate(_Model):
    candidateId: str
    label: str
    objectId: str | None = None
    observationIds: list[str] = Field(default_factory=list)
    kind: Literal["positional_change", "brightness_change", "both"]
    description: str
    evidence: list[str] = Field(default_factory=list)
    evidenceRefs: list[AIReference] | None = None
    track: list[TrackPoint] = Field(default_factory=list)
    motion: CandidateMotion | None = None
    properMotion: ProperMotion | None = None
    score: float = Field(ge=0.0, le=1.0)
    scoreBasis: str
    isProbability: Literal[False] = False
    qualityFlags: list[QualityFlag] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] | None = None
    status: Literal["candidate"] = "candidate"
    createdAt: str


class ApiErrorBody(_Model):
    code: Literal["INVALID_REQUEST", "NOT_FOUND", "UPSTREAM_UNAVAILABLE", "DATA_UNAVAILABLE", "PROCESSING_FAILED", "AI_UNAVAILABLE",
                  "RATE_LIMITED", "INTERNAL"]
    message: str
    retryable: bool
    details: dict | None = None
    requestId: str | None = None


class ApiError(_Model):
    error: ApiErrorBody


class CreateComparisonRequest(_Model):
    baseObservationId: str
    targetObservationId: str
    mode: Literal["blink", "overlay", "difference"]


class ComparisonResult(_Model):
    comparisonId: str
    mode: Literal["blink", "overlay", "difference"]
    baseObservationId: str
    targetObservationId: str
    comparisonKind: Literal["same_band_epochs", "cross_band"] | None = None
    availability: Availability
    compatibility: Compatibility
    alignment: Alignment | None = None
    baseImage: ObservationImage | None = None
    targetImage: ObservationImage | None = None
    differenceImage: ObservationImage | None = None
    measurements: list = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    createdAt: str
