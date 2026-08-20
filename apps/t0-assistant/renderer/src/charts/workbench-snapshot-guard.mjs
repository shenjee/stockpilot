import { ChartGroupKind, tryCreateChartGroupModel } from "./chart-model.mjs";
import { hasWorkbenchSnapshotEnvelope } from "../workbench-presenter.mjs";

/**
 * Ingress guard for Renderer workbench snapshots (#155 PR4).
 *
 * 1. ``hasWorkbenchSnapshotEnvelope`` — shallow shape only.
 * 2. Probe ``createChartGroupModel`` for both chart groups — deep render
 *    invariants (ordered bars, indicator/bar alignment). Failures keep the
 *    last good projection/chart; they must not throw into React render.
 *
 * Does not silently repair payloads. Deep schema ownership stays on Python.
 *
 * @param {unknown} candidate
 * @returns {{
 *   ok: true,
 *   snapshot: object,
 * } | {
 *   ok: false,
 *   reason: "envelope" | "contract",
 *   error?: unknown,
 * }}
 */
export function inspectWorkbenchSnapshotCandidate(candidate) {
  if (!hasWorkbenchSnapshotEnvelope(candidate)) {
    return { ok: false, reason: "envelope" };
  }
  for (const kind of [
    ChartGroupKind.FIVE_MINUTE,
    ChartGroupKind.ONE_MINUTE,
  ]) {
    const result = tryCreateChartGroupModel(candidate, kind);
    if (!result.ok) {
      return { ok: false, reason: "contract", error: result.error };
    }
  }
  return { ok: true, snapshot: candidate };
}

/**
 * @param {unknown} error
 * @returns {{
 *   error_code: string,
 *   message: string,
 *   retryable: boolean,
 *   affected_capability: string,
 * }}
 */
export function chartContractApplicationError(error) {
  const detail =
    error instanceof Error && error.message
      ? error.message
      : "图表载荷未通过渲染前置检查";
  return {
    error_code: "chart_contract_failed",
    message: `图表数据暂时不可用，已保留上一幅有效图形：${detail}`,
    retryable: true,
    affected_capability: "live",
  };
}
