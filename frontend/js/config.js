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
  contractVersion: "0.1.0",
  /** Simulated network latency for mocks (ms). 0 disables it. */
  mockLatencyMs: 250,
};
