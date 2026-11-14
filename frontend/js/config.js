// @ts-check
/**
 * Runtime configuration. Switching from mock data to the Python backend is
 * meant to be a change here only (backend readiness, Phase I).
 */
export const CONFIG = {
  /** true: use contract-shaped mock fixtures. false: call the real backend. */
  useMock: true,
  /** Base path of the real API (see docs/FRONTEND_BACKEND_CONTRACT.md §1). */
  apiBase: "/api/v1",
  /** Contract version this frontend was written against. */
  contractVersion: "0.2.0",
  /** Simulated network latency for mocks (ms). 0 disables it. */
  mockLatencyMs: 250,
  /** Real backend only: image field of view in arcminutes (1-30). undefined = backend default (10). Override per page with ?fov=15. */
  fovArcmin: undefined,
  /**
   * Real backend only: show ONE wavelength band at a time, because blinking across bands shows spectral differences,
   * not change over time. A band such as "SPHEREx-D3", or "all" for no filter. Override per page with ?band=SPHEREx-D2 or ?band=all.
   */
  bandpass: "SPHEREx-D3",
};
