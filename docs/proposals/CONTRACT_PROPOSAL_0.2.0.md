# Contract Proposal: v0.2.0 (candidates, frames, field of view)

> **Status: PROPOSAL from the frontend developer. Not agreed. Not part of the contract.**
> Per [DEVELOPMENT_OWNERSHIP.md](../DEVELOPMENT_OWNERSHIP.md#changing-the-contract), nothing here changes
> `FRONTEND_BACKEND_CONTRACT.md` or `DATA_MODELS.md` until the backend developer agrees.
> Every change below is an **optional addition**, so it is non-breaking (MINOR bump, 0.1.0 → 0.2.0).
> Until agreed, the frontend keeps mock-only equivalents in one file (`frontend/js/api/mock/proposed.js`),
> clearly labelled "ILLUSTRATIVE", and never presents them as real detections.

## Why

The product's core interaction is "spot the object that moved". The 0.1.0 contract can list observations and
compare two of them, but it cannot express (1) a candidate moving or varying object, (2) a playable sequence of
aligned frames, (3) a field-of-view request, or (4) a few display fields the approved design shows.
Scientific logic stays in the backend (rule 3 and 5); the frontend only displays what it receives.

## 1. `Candidate` model (new)

```ts
export interface Candidate {
  candidateId: Id;                       // app. Display label (e.g. "SX-0017") is `label`; IDs stay opaque.
  label: string;                         // short human-readable tag
  objectId?: Id;                         // object whose field this was found in
  observationIds: Id[];                  // observations the detection is based on (>= 2 for motion)
  kind: "positional_change" | "brightness_change" | "both";
  /** Position per observation, so the UI can draw the track without computing anything. */
  track: { observationId: Id; sky: SkyCoordinate; pixel?: { x: number; y: number } }[];
  /** Total apparent motion, if measured. */
  motion?: { value: number; unit: string; uncertainty?: number; method: string };
  /** Ranking score. NOT a probability. */
  score: number;                         // derived, in [0, 1]
  scoreBasis: string;                    // how the score was computed, shown to the user (e.g. "SNR of positional shift vs. alignment residual")
  qualityFlags: QualityFlag[];
  warnings: string[];                    // e.g. "Near a detector edge", "Only 2 epochs"
  status: "candidate";                   // literal. The API never reports "confirmed". Confirmation is outside the app.
  createdAt: ISODateTime;
}
```

Rules: `score` has no unit and must be shown with `scoreBasis`. `status` is always `"candidate"`. The AI companion
may not describe a candidate as a discovery (extends SCIENTIFIC_DATA_PIPELINE §9).

**Endpoint (new, #19):** `GET /objects/{objectId}/candidates` → `Page<Candidate>`
(`limit`, `offset`, `minScore`, `sort` = `-score` default). Empty result is 200 with `coverageNote`.

`ChatContext` gains optional `candidateId?: Id`; `AIReference.targetType` gains `"candidate"`;
`Bookmark.targetType` gains `"candidate"`.

## 2. Playable frame sequence (new)

Playback across N dates needs frames on **one common pixel grid**; pairwise `ComparisonResult` cannot supply that.

**Endpoint (new, #20):** `GET /objects/{objectId}/frames` → `FrameSequence`
(`from`, `to`, `dataRelease`, `stretch`; same `Availability`/202 polling rules as comparisons).

```ts
export interface FrameSequence {
  sequenceId: Id;
  objectId: Id;
  availability: Availability;
  alignment?: { method: string; residualArcsec?: number };
  frames: { observationId: Id; observedAt?: ISODateTime; image: ObservationImage; qualityFlags: QualityFlag[] }[];
  /** Observations left out and why (e.g. incompatible wavelength). Shown to the user. */
  excluded: { observationId: Id; reason: string }[];
  warnings: string[];
  generatedAt: ISODateTime;
}
```

All `frames[].image` share width/height/WCS. Compatibility rules from SCIENTIFIC_DATA_PIPELINE §5 apply and
reasons for exclusion are returned, not hidden.

## 3. Field of view (optional parameter)

`GET /observations/{id}/image` gains optional `centerRa`, `centerDec`, `fovArcmin`. If omitted, current behaviour.
The returned `ObservationImage` already carries `wcs`, which describes the delivered crop. Relates to OD-5.

## 4. Display fields from the approved design

- `AstronomicalObject.properMotion?: { value: number; unit: string; source: string }` (source = resolver/catalogue).
- `ChatContext.mode?: "beginner" | "advanced"` (optional hint so the backend may vary explanation depth;
  the backend may ignore it).

## 5. Open questions for the backend developer

1. Is candidate detection in scope for the backend within the hackathon time budget? If not, the frontend keeps
   candidates as clearly labelled mock data and the demo says so.
2. Is `score` realistic to produce with a documented `scoreBasis`?
3. Can frames be pre-computed for the demo objects (OD-12/OD-13)?
4. OK to bump to 0.2.0 and add a changelog row once agreed?

## 6. Frontend impact if agreed

Mocks gain the new shapes (already modelled in `proposed.js`), then move into the normal mock fixtures. No other
frontend code changes, because views consume the API client's return types only.
