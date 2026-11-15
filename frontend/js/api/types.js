// @ts-check
/**
 * JSDoc mirror of the canonical contract (docs/FRONTEND_BACKEND_CONTRACT.md §2-4, v0.1.0).
 * Editors type-check against these. The markdown contract remains the source of truth;
 * if they ever differ, the contract wins and this file is wrong.
 *
 * Only the models used so far are mirrored; more are added as milestones need them.
 */

/** @typedef {string} Id  Opaque. Never parse or construct. */
/** @typedef {{ raDeg: number, decDeg: number }} SkyCoordinate  ICRS degrees. */
/** @typedef {"available"|"processing"|"unavailable"|"failed"} AvailabilityStatus */
/** @typedef {{ status: AvailabilityStatus, statusReason?: string, retryAfterSeconds?: number }} Availability */
/** @typedef {{ code: string, severity: "info"|"warning"|"critical", description: string, origin: "source"|"derived" }} QualityFlag */

/**
 * @typedef {Object} AstronomicalObject
 * @property {Id} objectId
 * @property {string} name
 * @property {string[]} aliases
 * @property {SkyCoordinate} coordinates
 * @property {string} [objectType]
 * @property {{ name: string, identifier?: string }} resolver
 * @property {{ count: number, firstObservedAt?: string, lastObservedAt?: string }} [observationSummary]
 */

/**
 * @typedef {Object} Observation
 * @property {Id} observationId
 * @property {Id[]} objectIds
 * @property {string} [irsaObservationId]
 * @property {string} [dataRelease]
 * @property {string} [observedAt]  ISO 8601 UTC
 * @property {SkyCoordinate} center
 * @property {SkyCoordinate[]} [footprint]
 * @property {string} [detector]
 * @property {string} [bandpass]  archive bandpass name as reported; group same-band frames with it
 * @property {{ min: number, max: number }} [wavelengthRangeUm]
 * @property {Availability} image
 * @property {QualityFlag[]} qualityFlags
 * @property {"good"|"degraded"|"poor"|"unknown"} qualitySummary
 * @property {Record<string, unknown>} [sourceMetadata]
 */

/**
 * Contract 0.2.0. Catalogue and app-derived values are never mixed: see provenanceKind.
 * @typedef {Object} ProperMotion
 * @property {number} value
 * @property {string} unit
 * @property {string} source
 * @property {"catalog"|"derived_from_observations"|"unvalidated_displacement"} provenanceKind
 * @property {string} [catalogName]
 * @property {string} [catalogId]
 * @property {number} [pmRaMasPerYr]
 * @property {number} [pmDecMasPerYr]
 * @property {string} [convention]
 * @property {string} [referenceEpoch]
 * @property {number} [uncertainty]
 */

/**
 * Contract 0.2.0. A ranking for human follow-up: never a detection, never a probability.
 * @typedef {Object} Candidate
 * @property {Id} candidateId
 * @property {string} label
 * @property {Id} [objectId]
 * @property {Id[]} observationIds
 * @property {"positional_change"|"brightness_change"|"both"} kind
 * @property {string} description
 * @property {string[]} evidence
 * @property {{ observationId: Id, sky: SkyCoordinate, pixel?: { x: number, y: number } }[]} track  pixel is on the grid of that observation's image
 * @property {{ value: number, unit: string, uncertainty?: number, method: string }} [motion]
 * @property {ProperMotion} [properMotion]
 * @property {number} score  in [0, 1]; NOT a probability
 * @property {string} scoreBasis
 * @property {false} isProbability
 * @property {QualityFlag[]} qualityFlags
 * @property {string[]} warnings
 * @property {string[]} [limitations]
 * @property {"candidate"} status
 * @property {string} createdAt
 */

/**
 * @template T
 * @typedef {{ items: T[], total: number, limit: number, offset: number, coverageNote?: string }} Page
 */

/**
 * @typedef {Object} ApiErrorBody
 * @property {{ code: string, message: string, retryable: boolean, details?: Record<string, unknown>, requestId?: string }} error
 */

/** Thrown by the API client for every failed request, real or mock. */
export class ApiClientError extends Error {
  /**
   * @param {string} message user-safe text
   * @param {{ code?: string, retryable?: boolean, network?: boolean }} [opts]
   *   `network: true` means no response at all (contract §6): treat separately from an ApiError.
   */
  constructor(message, opts = {}) {
    super(message);
    this.name = "ApiClientError";
    this.code = opts.code ?? "INTERNAL";
    this.retryable = opts.retryable ?? false;
    this.network = opts.network ?? false;
  }
}
