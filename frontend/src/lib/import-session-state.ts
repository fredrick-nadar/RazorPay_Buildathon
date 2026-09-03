/** Status and detail share a single monotonically increasing request epoch. */
import type { FreshDemoResult, GatewayImportDetail, RazorpaySyncResult } from "./gateway-view";

export interface SessionStatusLike { gateway_import_id: string | null }
export interface ImportSessionState {
  requestId: number;
  refreshing: boolean;
  sessionStatus: SessionStatusLike | null;
  syncResult: RazorpaySyncResult | null;
  detail: GatewayImportDetail | null;
  freshDemo: FreshDemoResult | null;
}
export const INITIAL_IMPORT_SESSION_STATE: ImportSessionState = {
  requestId: 0, refreshing: false, sessionStatus: null,
  syncResult: null, detail: null, freshDemo: null,
};
export type ImportSessionEvent =
  | { type: "RESET" | "MUTATION_STARTED" | "REFRESH_STARTED" | "REFRESH_FAILED"; requestId: number }
  | { type: "REFRESH_LOADED"; requestId: number; status: SessionStatusLike; detail: GatewayImportDetail | null }
  | { type: "SYNC_SUCCEEDED"; requestId: number; result: RazorpaySyncResult }
  | { type: "DEMO_SUCCEEDED"; requestId: number; evidence: FreshDemoResult };

export function activeImportId(state: ImportSessionState): string | null {
  // A confirmed null link means no import, never a fallback to an old sync.
  return state.sessionStatus !== null
    ? state.sessionStatus.gateway_import_id
    : state.syncResult?.import_id ?? null;
}

export function importSessionReducer(state: ImportSessionState, event: ImportSessionEvent): ImportSessionState {
  if (event.type === "RESET" || event.type === "MUTATION_STARTED") {
    if (event.requestId <= state.requestId) return state;
    return { ...INITIAL_IMPORT_SESSION_STATE, requestId: event.requestId };
  }
  if (event.type === "REFRESH_STARTED") {
    if (event.requestId <= state.requestId) return state;
    return { ...state, requestId: event.requestId, refreshing: true, sessionStatus: null, detail: null };
  }
  if (event.requestId !== state.requestId) return state;
  switch (event.type) {
    case "REFRESH_LOADED": {
      const id = event.status.gateway_import_id;
      if (event.detail !== null && event.detail.import_id !== id) return state;
      return {
        ...state, refreshing: false, sessionStatus: event.status, detail: event.detail,
        syncResult: state.syncResult?.import_id === id ? state.syncResult : null,
        freshDemo: state.freshDemo?.importId === id ? state.freshDemo : null,
      };
    }
    case "REFRESH_FAILED":
      return { ...state, refreshing: false, sessionStatus: null, detail: null };
    case "SYNC_SUCCEEDED":
      return { ...state, syncResult: event.result };
    case "DEMO_SUCCEEDED":
      return { ...state, freshDemo: event.evidence };
  }
}
