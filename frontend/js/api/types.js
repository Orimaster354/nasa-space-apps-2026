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
 * @property {{ min: number, max: number }} [wavelengthRangeUm]
 * @property {Availability} image
 * @property {QualityFlag[]} qualityFlags
 * @property {"good"|"degraded"|"poor"|"unknown"} qualitySummary
 * @property {Record<string, unknown>} [sourceMetadata]
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
