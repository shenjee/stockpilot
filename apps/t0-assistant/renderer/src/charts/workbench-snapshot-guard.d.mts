import type { WorkbenchChartSnapshot } from "./chart-model.mjs";
import type { ApplicationError } from "../workbench-presenter.mjs";

export function inspectWorkbenchSnapshotCandidate(
  candidate: unknown,
):
  | { ok: true; snapshot: WorkbenchChartSnapshot }
  | { ok: false; reason: "envelope" | "contract"; error?: unknown };

export function chartContractApplicationError(
  error: unknown,
): ApplicationError;

export function chartEnvelopeApplicationError(): ApplicationError;
