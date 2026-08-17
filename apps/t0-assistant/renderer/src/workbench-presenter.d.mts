import type {
  MarketBar,
  WorkbenchChartSnapshot,
} from "./charts/chart-model.mjs";
import type { SecurityIdentity } from "./workbench-layout.mjs";

export interface ApplicationError {
  error_code: string;
  message: string;
  retryable: boolean;
  affected_capability?: string;
  operation_id?: string;
  /** Event/command ownership; Replay-channel failures set `source: "replay"`. */
  source?: "replay" | "live" | "service" | "preferences" | "symbol_selection";
}

export function standardSecurityFromResponse(
  response: unknown,
): SecurityIdentity | null;
export function restoredSecurityFromResponse(
  response: unknown,
): SecurityIdentity | null;
export function startupRestoreFromResponse(
  response: unknown,
): { status?: string; symbol?: string; session_id?: string | null } | null;
export function clearLiveScopedBackgroundError(
  error: ApplicationError | null,
): ApplicationError | null;
export function startupRestoreOperationId(sessionId: string): string;
export function cancelStartupRestoreTracking(
  restoreInFlight: { sessionId?: string | null } | null,
  activeOperations: Map<string, unknown>,
): void;
export function partialSecurityFromSymbol(symbol: string): SecurityIdentity | null;
export function securitiesFromSearchResponse(
  response: unknown,
): SecurityIdentity[];

/**
 * Map a standard security identity to a market classification label.
 *
 * Uses the authoritative `market` and `security_type` fields rather than
 * code-prefix inference, per issue #131:
 *   - security_type = etf           -> 基金 (covers SH/SZ listed ETFs only)
 *   - a_share + market = sh         -> 沪市
 *   - a_share + market = sz         -> 深市
 */
export function securityCategoryLabel(security: SecurityIdentity): string;

/**
 * Initial state for the security search box interaction reducer.
 */
export interface SecuritySearchState {
  activeIndex: number;
  dismissed: boolean;
}

export const initialSecuritySearchState: Readonly<SecuritySearchState>;

export type SecuritySearchAction =
  | { type: "arrow-down"; count: number }
  | { type: "arrow-up"; count: number }
  | { type: "escape"; visible: boolean }
  | { type: "mouse-enter"; index: number }
  | { type: "query-change" }
  | { type: "reset-cursor" }
  | { type: "select" };

/**
 * Pure reducer for security search box keyboard/mouse interaction.
 *
 * The "select" action always closes the dropdown immediately so that slow
 * or failed async callbacks in the parent do not leave the results list
 * visible.
 */
export function securitySearchReducer(
  state: SecuritySearchState,
  action: SecuritySearchAction,
): SecuritySearchState;

/**
 * Return the suggestion index that Enter would select, or null if there
 * are no suggestions to select.
 */
export function securitySearchEnterTarget(
  state: SecuritySearchState,
  count: number,
): number | null;

export function isCompleteWorkbenchSnapshot(
  candidate: unknown,
): candidate is WorkbenchChartSnapshot;
export function operationMatchesEnvelope(
  operation: { serviceGeneration: number; sessionId: string | null } | null,
  envelope: { service_generation: number | null; session_id: string | null },
): boolean;
export function createLatestRequestTracker(): Readonly<{
  begin(): number;
  isCurrent(candidate: number): boolean;
}>;
export function canHydratePreferences(
  status: { state: string },
  hydrated: boolean,
): boolean;
export function liveOperationFailurePresentation(
  mode: string,
  error: ApplicationError,
): { blocking: boolean; error: ApplicationError };
export function applicationErrorFrom(
  candidate: unknown,
): ApplicationError | null;
export function quoteRows(
  quote: unknown,
): Array<[label: string, value: string]>;
export function latestDailyBars(
  snapshot: WorkbenchChartSnapshot,
  limit?: number,
): MarketBar[];
