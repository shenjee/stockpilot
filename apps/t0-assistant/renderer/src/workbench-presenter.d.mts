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
}

export function standardSecurityFromResponse(
  response: unknown,
): SecurityIdentity | null;
export function securitiesFromSearchResponse(
  response: unknown,
): SecurityIdentity[];
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
