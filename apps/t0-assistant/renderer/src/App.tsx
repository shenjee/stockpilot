import {
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import chartFixture from "../../contracts/fixtures/chart-groups-v1.json";
import { ChartGroup } from "./charts/ChartGroup";
import {
  ChartGroupKind,
  createChartGroupModel,
  tryCreateChartGroupModel,
  type ChartGroupModel,
  type MarketBar,
  type WorkbenchChartSnapshot,
} from "./charts/chart-model.mjs";
import {
  chartContractApplicationError,
  chartEnvelopeApplicationError,
  inspectWorkbenchSnapshotCandidate,
} from "./charts/workbench-snapshot-guard.mjs";
import { selectActiveWorkbenchProjection } from "./charts/active-workbench-projection.mjs";
import {
  createChartProjection,
  type ChartProjection,
} from "./charts/chart-projection.mjs";
import { LiveProjectionController } from "./charts/live-projection-controller.mjs";
import { ReplaySessionController } from "./charts/replay-session-controller.mjs";
import { replayEventEnvelope } from "./replay-event-envelope.mjs";
import {
  applyWorkbenchPreferences,
  createWorkbenchState,
  selectWorkbenchLayout,
  selectWorkbenchMode,
  selectWorkbenchSecurity,
  toggleWorkbenchLayer,
  workbenchLayoutMode,
  workbenchPreferences,
  WorkbenchLayer,
  WorkbenchLayoutMode,
  WorkbenchMode,
  type ChartViewportSnapshot,
  type SecurityIdentity,
  type WorkbenchLayoutModeValue,
  type WorkbenchState,
} from "./workbench-layout.mjs";
import {
  applicationErrorFrom,
  cancelStartupRestoreTracking,
  canHydratePreferences,
  clearLiveScopedBackgroundError,
  createLatestRequestTracker,
  initialSecuritySearchState,
  latestDailyBars,
  liveOperationFailurePresentation,
  operationMatchesEnvelope,
  quoteDataCutoffText,
  quoteRows,
  restoredSecurityFromResponse,
  securitiesFromSearchResponse,
  securityCategoryLabel,
  securitySearchReducer,
  standardSecurityFromResponse,
  startupRestoreFromResponse,
  type ApplicationError,
} from "./workbench-presenter.mjs";
import { createSerialTaskQueue } from "./serial-task-queue.mjs";
import {
  REPLAY_SPEEDS,
  asReplayOwnedError,
  deriveReplayControls,
  isReplayOwnedError,
  marketClockLabel,
  marketTimeFromValue,
  replayFactsFromSnapshot,
  replaySessionMatches,
  type ReplayFacts,
} from "./replay-controls.mjs";
import {
  TradeDrawer,
  createBoundTradeClient,
  createInMemoryFeePlanClient,
} from "./trading/TradeDrawer";
import { createFeePlanClient } from "./trading/fee-plans.mjs";
import { createFeeAdvisor, createNullFeeAdvisor } from "./trading/fee-advisor.mjs";
import { isTradeScopedError } from "./trading/app-event-ownership.mjs";
import {
  TradeOperationController,
  type TradeOperationFailure,
} from "./trading/trade-operation-controller.mjs";
import {
  applyTradesChanged,
  filterTradesByReplayCursor,
  isRealTradesChangedEvent,
} from "./trading/trade-state.mjs";
import type { TradeRecord } from "./trading/trade-client.mjs";
import { projectTradeMarkers } from "./charts/trade-markers.mjs";

const initialStatus: ServiceStatus = {
  state: "starting",
  service_generation: 1,
  message: "正在启动本地服务…",
};

const emptyChartSnapshot: WorkbenchChartSnapshot = {
  timezone: "Asia/Shanghai",
  market: { bars_1m: [], bars_5m: [], daily_bars: [], quote: null },
  indicators: {
    five_minute: {
      ma: { ma5: [], ma10: [], ma20: [], ma30: [], ma60: [] },
      volume: { values: [], ma5: [], ma10: [] },
      macd: {
        fast_period: 12,
        slow_period: 26,
        signal_period: 9,
        dif: [],
        dea: [],
        histogram: [],
      },
    },
    one_minute: {
      vwap: [],
      volume: { values: [] },
      macd: {
        fast_period: 12,
        slow_period: 26,
        signal_period: 9,
        dif: [],
        dea: [],
        histogram: [],
      },
    },
  },
  chan_analysis: {
    strokes: [],
    pivot_zones: [],
    candidate_buy_points: [],
    candidate_sell_points: [],
  },
};

interface ActiveFailure {
  error: ApplicationError;
  retry: "security" | "live" | "service";
  security?: SecurityIdentity;
}

interface ActiveOperation {
  retry: ActiveFailure["retry"];
  security?: SecurityIdentity;
  serviceGeneration: number;
  sessionId: string | null;
}

function localToday() {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function tradeDateOf(executedAt: string) {
  return executedAt.length >= 10 ? executedAt.slice(0, 10) : null;
}

export function App() {
  const [status, setStatus] = useState<ServiceStatus>(initialStatus);
  const [workbench, setWorkbench] = useState<WorkbenchState>(
    createWorkbenchState,
  );
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<SecurityIdentity[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchMessage, setSearchMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [restoreMessage, setRestoreMessage] = useState<string | null>(null);
  const [backgroundError, setBackgroundError] =
    useState<ApplicationError | null>(null);
  const [activeFailure, setActiveFailure] = useState<ActiveFailure | null>(
    null,
  );
  const [preferencesHydrated, setPreferencesHydrated] = useState(false);
  const [preferenceHydrationAttempt, setPreferenceHydrationAttempt] =
    useState(0);
  const [replayDate, setReplayDate] = useState("");
  // T0-043 / Issue #163: scoped real-trade day list at App level so the 5m
  // chart overlay and Replay read-only day list share one authoritative source.
  const [realTrades, setRealTrades] = useState<{
    trades: TradeRecord[];
    tradeRevision: number;
    serviceGeneration: number | null;
    loadedScope: { symbol: string; tradeDate: string } | null;
  }>({
    trades: [],
    tradeRevision: -1,
    serviceGeneration: null,
    loadedScope: null,
  });
  const realTradesRef = useRef(realTrades);
  realTradesRef.current = realTrades;
  // T0-043 "进入当天图形": a dismissible notice when a historical trade's full
  // day chart cannot be restored (no frozen non-Replay command serves a static
  // historical workbench; see the T0-043 contract gap). Never wipes the last
  // successful chart.
  const [dayChartNotice, setDayChartNotice] = useState<string | null>(null);
  const lastGoodChartModels = useRef<{
    fiveMinute: ChartGroupModel;
    intraday: ChartGroupModel;
  } | null>(null);
  const activeOperations = useRef(new Map<string, ActiveOperation>());
  const modeRef = useRef(workbench.mode);
  const tradeScopeRef = useRef<{ symbol: string | null; tradeDate: string }>({
    symbol: null,
    tradeDate: localToday(),
  });
  const serviceGeneration = useRef(initialStatus.service_generation);
  const searchRequests = useRef(createLatestRequestTracker());
  const navigationRequests = useRef(createLatestRequestTracker());
  const preferenceHydrationInFlight = useRef(false);
  const restoreInFlight = useRef<{
    security: SecurityIdentity;
    sessionId: string;
    serviceGeneration: number;
  } | null>(null);
  const userModifiedPreferences = useRef(false);
  const preferenceSaveQueue = useRef(
    createSerialTaskQueue(async (preferences: ReturnType<typeof workbenchPreferences>) => {
      const response = await window.stockpilot.savePreferences(
        appRequest("save_preferences", null, { preferences }),
      );
      const error = applicationErrorFrom(response);
      if (error) throw error;
      return response;
    }),
  );
  // 正式环境中的成交、收费方案和费用建议均经 Safe Bridge 访问 Python 权威
  // 服务；fixture 模式保留内存方案和 null 顾问，不复制收费公式。
  const tradeClient = useMemo(
    () => (window.stockpilot ? createBoundTradeClient(window.stockpilot) : null),
    [],
  );
  const feePlanClient = useMemo(
    () =>
      window.stockpilot
        ? createFeePlanClient(window.stockpilot)
        : createInMemoryFeePlanClient(),
    [],
  );
  const feeAdvisor = useMemo(
    () =>
      window.stockpilot
        ? createFeeAdvisor(window.stockpilot)
        : createNullFeeAdvisor(),
    [],
  );
  const subscribeAppEvent = useMemo(
    () =>
      window.stockpilot ? window.stockpilot.onAppEvent.bind(window.stockpilot) : null,
    [],
  );
  // Persistent trade-operation registry + failure surface. Owned at the App
  // level (always mounted) so a trade op started in Live that fails after the
  // user switches to Replay is still surfaced with the correct CRUD retry, and
  // the App never silently drops a trades-scoped operation_failed. The
  // TradeDrawer delegates its track/resolve/fail to this controller.
  const tradeOpController = useRef<TradeOperationController | null>(null);
  if (tradeOpController.current === null) {
    tradeOpController.current = new TradeOperationController();
  }
  const liveProjectionController = useRef<LiveProjectionController | null>(
    null,
  );
  if (liveProjectionController.current === null) {
    liveProjectionController.current = new LiveProjectionController(
      createChartProjection(
        window.stockpilot
          ? emptyChartSnapshot
          : (chartFixture as unknown as WorkbenchChartSnapshot),
      ),
    );
  }
  const replaySessionController = useRef<ReplaySessionController | null>(
    null,
  );
  if (replaySessionController.current === null) {
    replaySessionController.current = new ReplaySessionController();
  }
  const [, bumpProjection] = useReducer((n: number) => n + 1, 0);
  // Multiple trade operations may fail concurrently; the controller keeps them
  // all (keyed by operation id) so a later failure never overwrites an earlier
  // one's retry. The App renders every failure, each with its own retry/dismiss.
  const [tradeFailures, setTradeFailures] = useState<TradeOperationFailure[]>(
    [],
  );
  useEffect(() => {
    const controller = tradeOpController.current;
    if (!controller) return;
    return controller.subscribe((failures) => setTradeFailures(failures));
  }, []);

  useEffect(() => {
    const live = liveProjectionController.current;
    const replay = replaySessionController.current;
    if (!live || !replay) return;
    const unsubLive = live.subscribe(() => bumpProjection());
    const unsubReplay = replay.subscribe(() => bumpProjection());
    return () => {
      unsubLive();
      unsubReplay();
    };
  }, []);

  useEffect(() => {
    modeRef.current = workbench.mode;
  }, [workbench.mode]);

  const liveCtrl = liveProjectionController.current!;
  const replayCtrl = replaySessionController.current!;
  replayCtrl.setServiceGeneration(serviceGeneration.current);

  const projection =
    selectActiveWorkbenchProjection({
      mode: workbench.mode,
      liveProjection: liveCtrl.projection,
      replayProjection: replayCtrl.projection,
      loadingFallbackProjection: replayCtrl.loadingFallbackProjection,
    }) ?? liveCtrl.projection;

  const snapshot = projection.snapshot;
  const replayFacts = useMemo(
    () =>
      replayCtrl.hasAuthoritativeProjection
        ? replayFactsFromSnapshot(replayCtrl.projection!.snapshot)
        : null,
    [
      workbench.mode,
      replayCtrl.projection,
      replayCtrl.hasAuthoritativeProjection,
    ],
  );

  useEffect(() => {
    if (!window.stockpilot) {
      setStatus({
        state: "disconnected",
        service_generation: 0,
        message: "Renderer fixture 模式",
      });
      setPreferencesHydrated(true);
      return;
    }
    const updateServiceStatus = (next: ServiceStatus) => {
      const generationChanged =
        serviceGeneration.current > 0 &&
        next.service_generation > 0 &&
        serviceGeneration.current !== next.service_generation;
      serviceGeneration.current = next.service_generation;
      setStatus(next);
      if (generationChanged) {
        replaySessionController.current?.clearForGenerationChange();
        replaySessionController.current?.setServiceGeneration(
          next.service_generation,
        );
        setReplayDate("");
        setRealTrades({
          trades: [],
          tradeRevision: -1,
          serviceGeneration: next.service_generation,
          loadedScope: null,
        });
      }
      if (next.state === "ready" || next.state === "connected") {
        setBackgroundError((current) =>
          current?.affected_capability === "service" ? null : current,
        );
      } else if (next.state === "failed" || next.state === "disconnected") {
        setBackgroundError(serviceStatusError(next));
      }
      if (next.service_generation <= 0) return;
      if (generationChanged) {
        activeOperations.current.clear();
        // Drop stale pending trade operations from the previous generation;
        // the new generation's revisions restart. The controller itself stays
        // mounted (it only clears its pending map, not its failure surface).
        tradeOpController.current?.clearPending();
        liveProjectionController.current?.resetForGeneration(
          emptyChartSnapshot,
          next.service_generation,
        );
        return;
      }
      const live = liveProjectionController.current;
      if (
        live &&
        live.projection.serviceGeneration !== null &&
        live.projection.serviceGeneration !== next.service_generation
      ) {
        activeOperations.current.clear();
        live.resetForGeneration(emptyChartSnapshot, next.service_generation);
      }
    };
    void window.stockpilot.getServiceStatus().then(updateServiceStatus);
    const stopStatus = window.stockpilot.onServiceStatus(updateServiceStatus);

    return stopStatus;
  }, []);

  useEffect(() => {
    if (
      !window.stockpilot ||
      !canHydratePreferences(status, preferencesHydrated) ||
      preferenceHydrationInFlight.current
    ) {
      return;
    }
    let cancelled = false;
    preferenceHydrationInFlight.current = true;
    void window.stockpilot
      .getPreferences(appRequest("get_preferences", null, {}))
      .then(async (response) => {
        const error = applicationErrorFrom(response);
        if (error) throw error;
        const preferences = preferencesFromResponse(response);
        if (!preferences) {
          throw new TypeError("偏好响应缺少有效设置");
        }
        if (!userModifiedPreferences.current) {
          setWorkbench((current) =>
            applyWorkbenchPreferences(current, preferences),
          );
          if (preferences.last_symbol) {
            const startup = startupRestoreFromResponse(response);
            const resolvedSecurity = restoredSecurityFromResponse(response);
            const displaySecurity = resolvedSecurity;
            if (
              displaySecurity &&
              !cancelled &&
              !userModifiedPreferences.current
            ) {
              setWorkbench((current) =>
                selectWorkbenchSecurity(current, displaySecurity),
              );
              setQuery(displaySecurity.code);
              setRestoreMessage(
                displaySecurity.name
                  ? `正在恢复上次查看的 ${displaySecurity.name}（${displaySecurity.code}）…`
                  : `正在恢复上次查看的 ${displaySecurity.code}…`,
              );
              setLoading(true);
              const restoreStatus = startup?.status;
              const sessionId =
                typeof startup?.session_id === "string"
                  ? startup.session_id
                  : null;
              if (
                resolvedSecurity &&
                (restoreStatus === "restored" ||
                  restoreStatus === "already_active")
              ) {
                await applyStartupRestore(resolvedSecurity, sessionId);
              } else if (resolvedSecurity) {
                await performSecuritySelection(resolvedSecurity, true);
              } else if (restoreStatus === "invalid_symbol") {
                setLoading(false);
                setRestoreMessage(null);
                setBackgroundError({
                  error_code: "invalid_request",
                  message: `上次保存的股票 ${preferences.last_symbol} 已无法识别，请重新选择`,
                  retryable: false,
                  affected_capability: "symbol_selection",
                });
              }
            }
          }
        }
        if (!cancelled) {
          setBackgroundError((current) =>
            current?.affected_capability === "preferences" ? null : current,
          );
          setPreferencesHydrated(true);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setBackgroundError(clientError(error, "preferences"));
        }
      })
      .finally(() => {
        preferenceHydrationInFlight.current = false;
      });
    return () => {
      cancelled = true;
    };
  }, [
    status.state,
    status.service_generation,
    preferencesHydrated,
    preferenceHydrationAttempt,
  ]);

  useEffect(() => {
    if (!window.stockpilot) return;
    return window.stockpilot.onAppEvent((event) => {
      const envelope = eventEnvelope(event);
      if (!envelope) return;
      const applyLiveEvent = () => {
        if (!isChartAppEvent(event)) return;
        liveProjectionController.current?.applyEvent(event);
      };
      if (envelope.event_type === "live_session_status") {
        const state = (envelope.payload as { state?: unknown })?.state;
        const liveIsVisible = modeRef.current !== WorkbenchMode.REPLAY;
        if (envelope.operation_id && envelope.session_id) {
          const active = activeOperations.current.get(envelope.operation_id);
          if (
            active &&
            active.serviceGeneration === envelope.service_generation &&
            active.sessionId === null
          ) {
            activeOperations.current.set(envelope.operation_id, {
              ...active,
              sessionId: envelope.session_id,
            });
          }
        }
        if (liveIsVisible && (state === "loading" || state === "created")) {
          setLoading(true);
        }
        if (state === "ready") {
          if (liveIsVisible) {
            setLoading(false);
            setRestoreMessage(null);
            if (
              restoreInFlight.current &&
              envelope.session_id === restoreInFlight.current.sessionId
            ) {
              restoreInFlight.current = null;
            }
          }
          setBackgroundError((current) => clearLiveScopedBackgroundError(current));
        }
        applyLiveEvent();
        return;
      }
      if (envelope.event_type === "operation_failed") {
        const error = applicationErrorFrom(envelope.payload);
        if (!error) return;
        if (isTradeScopedError(error)) {
          // Trade operation failures are owned by the persistent
          // TradeOperationController (not the generic retryLive/retryService
          // path). The controller survives Live/Replay mode switches, so a
          // trade op started in Live that fails after the user switched to
          // Replay is still surfaced with the correct CRUD retry and never
          // silently dropped.
          const opId =
            typeof envelope.operation_id === "string"
              ? envelope.operation_id
              : null;
          const message = error.message;
          const controller = tradeOpController.current;
          if (controller) {
            if (opId && controller.has(opId)) {
              controller.fail(opId, message, error);
            } else {
              // Untracked (e.g. event arrived before the op was registered, or
              // the Drawer unmounted and dropped tracking). Pass the opId so a
              // later track() for the same id can merge in the retry; until
              // then the failure is visible with a null retry. Never silently
              // dropped.
              controller.failUntracked(opId, message, error);
            }
          }
          applyLiveEvent();
          return;
        }
        const candidate = envelope.operation_id
          ? activeOperations.current.get(envelope.operation_id)
          : undefined;
        const active = operationMatchesEnvelope(candidate ?? null, envelope)
          ? candidate
          : undefined;
        if (envelope.operation_id && candidate) {
          activeOperations.current.delete(envelope.operation_id);
        }
        if (active) {
          const presentation = liveOperationFailurePresentation(
            modeRef.current,
            error,
          );
          if (presentation.blocking) {
            setLoading(false);
            setActiveFailure({
              error: presentation.error,
              retry: active.retry,
              security: active.security,
            });
          } else {
            setBackgroundError(presentation.error);
          }
        } else {
          const pendingRestore = restoreInFlight.current;
          if (
            pendingRestore &&
            envelope.service_generation === pendingRestore.serviceGeneration &&
            envelope.session_id === pendingRestore.sessionId
          ) {
            restoreInFlight.current = null;
            setLoading(false);
            setRestoreMessage(null);
            setActiveFailure({
              error,
              retry: "security",
              security: pendingRestore.security,
            });
          } else {
            setBackgroundError(error);
          }
        }
        applyLiveEvent();
        return;
      }
      if (envelope.event_type === "preferences_changed") {
        // A persistence acknowledgement never replaces React's newer runtime UI.
        applyLiveEvent();
        return;
      }
      if (envelope.event_type === "trades_changed") {
        // Trade-list updates are not chart events and must not be routed to
        // the workbench projection. Resolve any pending trade operation the
        // controller is tracking via the event's operation_id.
        const opId =
          typeof envelope.operation_id === "string"
            ? envelope.operation_id
            : null;
        if (opId) tradeOpController.current?.resolve(opId);
        // Scoped day list for the current symbol + trade_date (Issue #163).
        const scope = tradeScopeRef.current;
        if (scope.symbol && isRealTradesChangedEvent(event)) {
          setRealTrades((current) =>
            applyTradesChanged(current, event, {
              symbol: scope.symbol as string,
              tradeDate: scope.tradeDate,
            }),
          );
        }
        applyLiveEvent();
        return;
      }
      const baseline = chartProjectionFromEvent(event);
      if (baseline) {
        liveProjectionController.current?.clearRebaselineRequest();
        liveProjectionController.current?.applySnapshot(
          baseline.snapshot,
          projectionIdentity(baseline),
        );
        if (
          restoreInFlight.current &&
          restoreInFlight.current.sessionId === baseline.sessionId
        ) {
          restoreInFlight.current = null;
          setRestoreMessage(null);
        }
        activeOperations.current.clear();
        setLoading(false);
        setBackgroundError((current) => clearLiveScopedBackgroundError(current));
        return;
      }
      // Invalid or incomplete workbench_snapshot must never enter the incremental
      // reducer: that would advance revision while keeping the old snapshot body
      // (#155 review P1). Always surface an error and request rebaseline.
      if (
        event &&
        typeof event === "object" &&
        (event as { event_type?: unknown }).event_type === "workbench_snapshot"
      ) {
        const inspected = inspectWorkbenchSnapshotCandidate(
          (event as { payload?: unknown }).payload,
        );
        if (!inspected.ok && inspected.reason === "contract") {
          setBackgroundError(chartContractApplicationError(inspected.error));
        } else {
          setBackgroundError(chartEnvelopeApplicationError());
        }
        liveProjectionController.current?.requestRebaseline();
        return;
      }
      applyLiveEvent();
    });
  }, []);

  useEffect(() => {
    if (!window.stockpilot) return;
    // replay-event is the sole authoritative Replay ingress. Gateway also
    // emits bare replay-snapshot payloads (no service_generation / outer
    // session_id / operation_id); those must not write the controller or they
    // would bypass the identity gate and overwrite Replay-owned errors
    // (#155 / #162 review P1).
    const stopEvent = window.stockpilot.onReplayEvent((event) => {
      if (modeRef.current !== WorkbenchMode.REPLAY) return;
      const envelope = replayEventEnvelope(event);
      if (!envelope) return;
      const replay = replaySessionController.current;
      if (!replay) return;
      if (
        envelope.service_generation !== serviceGeneration.current ||
        !replaySessionMatches(replay.sessionId, envelope.session_id)
      ) {
        return;
      }
      if (envelope.event_type === "operation_failed") {
        const error = applicationErrorFrom(envelope.payload);
        // Ownership comes from the Replay event channel, not affected_capability.
        if (error) setBackgroundError(asReplayOwnedError(error));
        if (replay.matchesLoadOperation(envelope.operation_id)) {
          replay.failLoadOperation();
        }
        const cursorNote = replay.noteCursorOutcome(
          typeof envelope.operation_id === "string"
            ? envelope.operation_id
            : null,
          "failed",
        );
        if (cursorNote === "settled") {
          replay.setResumeAfterSeek(false);
        }
        return;
      }
      if (envelope.event_type === "workbench_snapshot") {
        const operationId =
          typeof envelope.operation_id === "string"
            ? envelope.operation_id
            : null;
        const inspected = inspectWorkbenchSnapshotCandidate(envelope.payload);
        if (!inspected.ok) {
          setBackgroundError(
            asReplayOwnedError(
              inspected.reason === "contract"
                ? chartContractApplicationError(inspected.error)
                : chartEnvelopeApplicationError(),
            ),
          );
          if (replay.matchesLoadOperation(operationId)) {
            replay.failLoadOperation();
          } else {
            const cursorNote = replay.noteCursorOutcome(operationId, "failed");
            if (cursorNote === "settled") {
              replay.setResumeAfterSeek(false);
            }
          }
          return;
        }
        // Outer envelope identity is authoritative; do not fill revision from
        // payload. acceptSnapshot also requires payload session.revision to
        // match (#162 review P2).
        const accepted = replay.acceptSnapshot(inspected.snapshot, {
          service_generation: envelope.service_generation,
          session_id: envelope.session_id,
          revision: envelope.revision,
          operation_id: operationId,
        });
        if (!accepted) {
          // Identity/operation mismatch: keep loading/fallback; do not settle.
          return;
        }
        const cursorNote = replay.noteCursorOutcome(operationId, "completed");
        if (cursorNote === "settled") {
          if (replay.takeResumeAfterSeek()) {
            requestReplayPlayback(
              replay.sessionId,
              true,
              (error) => setBackgroundError(error),
            );
          }
        }
        return;
      }
      if (envelope.event_type !== "session_status") return;
      const payload = envelope.payload as {
        state?: unknown;
        playback_speed?: unknown;
      };
      replay.applySessionStatus({
        state: payload.state,
        playback_speed: payload.playback_speed,
        revision: envelope.revision,
      });
    });
    return () => {
      stopEvent();
    };
  }, []);

  useEffect(() => {
    // Live rebaseline belongs to the Live controller lifecycle and must keep
    // running while Replay is foreground (#155 review P2).
    if (!window.stockpilot) return;
    const live = liveProjectionController.current;
    if (!live) return;
    const liveProjection = live.projection;
    if (
      !liveProjection.rebaselineRequired ||
      liveProjection.serviceGeneration === null ||
      liveProjection.sessionId === null ||
      liveProjection.revision === null
    ) {
      return;
    }
    const requestKey = [
      liveProjection.serviceGeneration,
      liveProjection.sessionId,
      liveProjection.revision,
    ].join(":");
    if (!live.beginRebaselineRequest(requestKey)) return;
    const requestedProjection = liveProjection;
    void window.stockpilot
      .getLiveSnapshot(
        appRequest("get_live_snapshot", liveProjection.sessionId, {}),
      )
      .then((response) => {
        const candidate = workbenchSnapshotFromResponse(response);
        if (!candidate) return;
        const current = live.projection;
        if (
          !current.rebaselineRequired ||
          current.serviceGeneration !==
            requestedProjection.serviceGeneration ||
          current.sessionId !== requestedProjection.sessionId
        ) {
          return;
        }
        live.applySnapshot(candidate, {
          service_generation:
            requestedProjection.serviceGeneration ?? undefined,
          session_id: requestedProjection.sessionId,
          revision: candidate.session?.revision,
        });
      })
      .catch(() => {
        // The gateway also owns the bounded rebaseline path.
      });
  }, [liveCtrl.projection]);

  useEffect(() => {
    if (!window.stockpilot || !preferencesHydrated) return;
    const timer = window.setTimeout(() => {
      const preferences = workbenchPreferences(workbench);
      void preferenceSaveQueue.current
        .enqueue(preferences)
        .then(() =>
          setBackgroundError((current) =>
            current?.affected_capability === "preferences" ? null : current,
          ),
        )
        .catch((error) =>
          setBackgroundError(clientError(error, "preferences")),
        );
    }, 250);
    return () => window.clearTimeout(timer);
  }, [
    preferencesHydrated,
    workbench.security?.symbol,
    workbench.layout.chartSplit,
    workbench.layout.showIntraday,
    workbench.layers,
  ]);

  useEffect(() => {
    if (!window.stockpilot) return;
    const sequence = searchRequests.current.begin();
    const text = query.trim();
    if (!text || text === workbench.security?.code) {
      setSuggestions([]);
      setSearchMessage("");
      setSearching(false);
      return;
    }
    // Clear stale results from the previous query immediately so that
    // keyboard handlers (ArrowUp/Down/Enter) cannot select invisible
    // results during the debounce or network window.
    setSuggestions([]);
    setSearchMessage("");
    setSearching(true);
    const timer = window.setTimeout(() => {
      void searchSecurityMatches(text)
        .then((matches) => {
          if (!searchRequests.current.isCurrent(sequence)) return;
          setSuggestions(matches);
          setSearchMessage(
            matches.length > 0 ? "" : "没有匹配的沪深股票或场内 ETF",
          );
        })
        .catch((error) => {
          if (!searchRequests.current.isCurrent(sequence)) return;
          setSuggestions([]);
          setSearchMessage(clientError(error, "symbol_selection").message);
        })
        .finally(() => {
          if (searchRequests.current.isCurrent(sequence)) setSearching(false);
        });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [query, workbench.security?.code]);

  async function searchSecurityMatches(input: string) {
    const response = await window.stockpilot.searchSecurities(
      appRequest("search_securities", null, { query: input, limit: 20 }),
    );
    const error = applicationErrorFrom(response);
    if (error) throw error;
    return securitiesFromSearchResponse(response);
  }

  async function applyStartupRestore(
    security: SecurityIdentity,
    sessionId: string | null,
  ) {
    const selectionSequence = navigationRequests.current.begin();
    if (!window.stockpilot) {
      setWorkbench((current) => selectWorkbenchSecurity(current, security));
      setQuery(security.code);
      setLoading(false);
      setRestoreMessage(null);
      return;
    }
    setWorkbench((current) => selectWorkbenchSecurity(current, security));
    setQuery(security.code);
    setSuggestions([]);
    setSearchMessage("");
    if (sessionId) {
      const operationId = `live-load-${sessionId}`;
      restoreInFlight.current = {
        security,
        sessionId,
        serviceGeneration: status.service_generation,
      };
      activeOperations.current.set(operationId, {
        retry: "security",
        security,
        serviceGeneration: status.service_generation,
        sessionId,
      });
      try {
        const snapshotResponse = await window.stockpilot.getLiveSnapshot(
          appRequest("get_live_snapshot", sessionId, {}),
        );
        if (!navigationRequests.current.isCurrent(selectionSequence)) return;
        const recovered = workbenchSnapshotFromResponse(snapshotResponse);
        if (recovered) {
          liveProjectionController.current?.applySnapshot(recovered, {
            service_generation: status.service_generation,
            session_id: sessionId,
            revision: recovered.session?.revision,
          });
          setLoading(false);
          setRestoreMessage(null);
          restoreInFlight.current = null;
          activeOperations.current.delete(`live-load-${sessionId}`);
          return;
        }
      } catch {
        // The event channel may still deliver the startup snapshot.
      }
      const live = liveProjectionController.current;
      if (
        live &&
        (live.projection.serviceGeneration !== status.service_generation ||
          live.projection.sessionId !== sessionId)
      ) {
        live.beginSession(
          live.projection.snapshot,
          status.service_generation,
          sessionId,
        );
      }
    }
  }

  // Issue #151: resolve the authoritative SecurityIdentity for a symbol via
  // select_security without switching the workbench. Used by HistoryTradesDialog
  // when editing a historical trade whose symbol differs from the current
  // workbench security, so the fee advisor receives the correct
  // instrument_type instead of a fabricated default.
  async function resolveSecurity(
    symbol: string,
  ): Promise<SecurityIdentity | null> {
    if (!window.stockpilot) return null;
    try {
      const response = await window.stockpilot.resolveSecurityIdentity(
        appRequest("resolve_security_identity", null, { symbol }),
      );
      const error = applicationErrorFrom(response);
      if (error) return null;
      return standardSecurityFromResponse(response);
    } catch {
      return null;
    }
  }

  async function performSecuritySelection(
    security: SecurityIdentity,
    restoring = false,
  ): Promise<boolean> {
    const selectionSequence = navigationRequests.current.begin();
    cancelStartupRestoreTracking(
      restoreInFlight.current,
      activeOperations.current,
    );
    restoreInFlight.current = null;
    const replayOwnsView = modeRef.current === WorkbenchMode.REPLAY;
    if (!restoring) userModifiedPreferences.current = true;
    if (!window.stockpilot) {
      setWorkbench((current) =>
        selectWorkbenchSecurity(current, security),
      );
      return true;
    }
    if (!replayOwnsView) {
      setActiveFailure(null);
      setLoading(true);
    } else {
      // Replay owns the visible workbench. The selected security is shared
      // with Live, but choosing it must not pretend that Replay history is
      // loading before the user has selected a date and started Replay.
      setWorkbench((current) =>
        selectWorkbenchSecurity(current, security),
      );
      setQuery(security.code);
      setSuggestions([]);
      setSearchMessage("");
    }
    try {
      const response = await window.stockpilot.selectSecurity(
        appRequest("select_security", null, { symbol: security.symbol }),
      );
      if (!navigationRequests.current.isCurrent(selectionSequence)) return false;
      const error = applicationErrorFrom(response);
      if (error) {
        const presentation = liveOperationFailurePresentation(
          modeRef.current,
          error,
        );
        if (restoring) {
          setLoading(false);
          setRestoreMessage(null);
          setActiveFailure({
            error: presentation.error,
            retry: "security",
            security,
          });
        } else if (presentation.blocking) {
          setLoading(false);
          setActiveFailure({
            error: presentation.error,
            retry: "security",
            security,
          });
        } else {
          setBackgroundError(presentation.error);
        }
        return false;
      }
      const operationId = responseOperationId(response);
      const sessionId = responseSessionId(response);
      const preferenceWarning = preferenceWarningFromResponse(response);
      if (preferenceWarning) {
        setBackgroundError(preferenceWarning);
      }
      // Issue #151: the backend resolves the authoritative identity once;
      // the renderer must adopt it rather than continuing to use the pre-call
      // object, which may have a wrong or missing instrument_type.
      const authoritative = standardSecurityFromResponse(response);
      const resolvedSecurity = authoritative ?? security;
      activeOperations.current.clear();
      liveProjectionController.current?.clearRebaselineRequest();
      const live = liveProjectionController.current;
      if (live) {
        const current = live.projection;
        if (
          current.serviceGeneration !== status.service_generation ||
          current.sessionId !== sessionId ||
          current.snapshot.session?.symbol !== resolvedSecurity.symbol ||
          current.snapshot.session?.state !== "ready"
        ) {
          live.beginSession(
            current.snapshot,
            status.service_generation,
            sessionId,
          );
        }
        if (modeRef.current !== WorkbenchMode.REPLAY) {
          const updated = live.projection;
          if (
            updated.serviceGeneration === status.service_generation &&
            updated.sessionId === sessionId &&
            updated.snapshot.session?.symbol === resolvedSecurity.symbol &&
            updated.snapshot.session?.state === "ready"
          ) {
            setLoading(false);
          }
        }
      }
      if (operationId) {
        activeOperations.current.set(operationId, {
          retry: "security",
          security: resolvedSecurity,
          serviceGeneration: status.service_generation,
          sessionId,
        });
      }
      setWorkbench((current) =>
        selectWorkbenchSecurity(current, resolvedSecurity),
      );
      setQuery(resolvedSecurity.code);
      setSuggestions([]);
      setSearchMessage("");
      // Recover the authoritative baseline when it raced ahead of the command
      // response or the event channel. A cold load may not be ready yet; in
      // that normal case the later workbench_snapshot event remains the owner.
      if (sessionId) {
        const snapshotResponse = await window.stockpilot.getLiveSnapshot(
          appRequest("get_live_snapshot", sessionId, {}),
        );
        if (!navigationRequests.current.isCurrent(selectionSequence)) return false;
        const recovered = workbenchSnapshotFromResponse(snapshotResponse);
        if (recovered) {
          const identity = {
            service_generation: status.service_generation,
            session_id: sessionId,
            revision: recovered.session?.revision,
          };
          liveProjectionController.current?.applySnapshot(recovered, identity);
          if (modeRef.current !== WorkbenchMode.REPLAY) {
            setLoading(false);
          }
          activeOperations.current.delete(operationId ?? "");
        }
      }
      return liveProjectionReadyForSymbol(resolvedSecurity.symbol);
    } catch (error) {
      if (!navigationRequests.current.isCurrent(selectionSequence)) return false;
      const failure = clientError(error, "symbol_selection");
      const presentation = liveOperationFailurePresentation(
        modeRef.current,
        failure,
      );
      if (restoring) {
        setLoading(false);
        setRestoreMessage(null);
        setActiveFailure({
          error: presentation.error,
          retry: "security",
          security,
        });
      } else if (presentation.blocking) {
        setLoading(false);
        setActiveFailure({
          error: presentation.error,
          retry: "security",
          security,
        });
      } else {
        setBackgroundError(presentation.error);
      }
      return false;
    }

    function liveProjectionReadyForSymbol(symbol: string): boolean {
      const projection = liveProjectionController.current?.projection;
      return Boolean(
        projection &&
          projection.serviceGeneration === status.service_generation &&
          projection.snapshot.session?.symbol === symbol &&
          projection.snapshot.session?.state === "ready",
      );
    }
  }

  // T0-043 "进入当天图形": restore a historical trade's full trading-day chart
  // without starting Replay playback. Today's trades reuse the Live workbench
  // (switch security -> today's bars + today's real-trade markers, which the
  // TradeDrawer already scopes). A historical trading day loads a complete,
  // static workbench snapshot via get_historical_snapshot and replaces the
  // current projection with it.
  // When opened from Replay, exit Replay only after the target chart is ready
  // so identity/snapshot failures keep the Replay session and projection.
  async function handleEnterDayChart(symbol: string, tradeDate: string) {
    const requestSequence = navigationRequests.current.begin();
    const today = localToday();
    // Resolve identity before entering today's Live chart or loading a
    // historical snapshot. Identity lookup itself never changes Live state.
    if (tradeDate === today) {
      setDayChartNotice(null);
      const identity = await resolveSecurity(symbol);
      if (!identity || !navigationRequests.current.isCurrent(requestSequence)) {
        setDayChartNotice("证券身份解析失败，无法进入当天图形。");
        return;
      }
      // Prepare Live while Replay still owns the view; only leave Replay after
      // the Live projection is ready for this symbol.
      const liveReady = await performSecuritySelection(identity);
      if (!navigationRequests.current.isCurrent(requestSequence)) return;
      if (!liveReady) {
        setDayChartNotice("当天图形加载失败，已保留当前图形。");
        return;
      }
      if (modeRef.current === WorkbenchMode.REPLAY) {
        selectMode(WorkbenchMode.LIVE);
      }
      return;
    }

    if (!window.stockpilot) {
      if (!navigationRequests.current.isCurrent(requestSequence)) return;
      setDayChartNotice(
        `该交易日（${tradeDate}）的完整历史图形暂不可用，已保留当前图形。`,
      );
      return;
    }

    if (!navigationRequests.current.isCurrent(requestSequence)) return;
    setLoading(true);
    setDayChartNotice(null);
    try {
      // Resolve the authoritative identity before loading the historical
      // snapshot so the workbench carries the correct instrument_type.
      let identity: SecurityIdentity;
      if (workbench.security && workbench.security.symbol === symbol) {
        identity = workbench.security;
      } else {
        const authoritative = await resolveSecurity(symbol);
        if (!authoritative) {
          setLoading(false);
          setDayChartNotice(
            `该交易日（${tradeDate}）的证券信息不完整，已保留当前图形。`,
          );
          return;
        }
        identity = authoritative;
      }
      const response = await window.stockpilot.getHistoricalSnapshot(
        appRequest("get_historical_snapshot", null, {
          symbol,
          trade_date: tradeDate,
        }),
      );
      if (!navigationRequests.current.isCurrent(requestSequence)) return;
      const error = applicationErrorFrom(response);
      if (error) {
        setLoading(false);
        setDayChartNotice(
          `该交易日（${tradeDate}）的历史图形加载失败：${error.message}`,
        );
        return;
      }
      const responseData = (response as { data?: unknown }).data;
      const inspected = inspectWorkbenchSnapshotCandidate(responseData);
      const session = inspected.ok ? inspected.snapshot.session : undefined;
      if (!inspected.ok || !session) {
        setLoading(false);
        if (inspected.ok === false && inspected.reason === "contract") {
          setBackgroundError(chartContractApplicationError(inspected.error));
          setDayChartNotice(
            `该交易日（${tradeDate}）的历史图形载荷无效，已保留当前图形。`,
          );
        } else {
          setDayChartNotice(
            `该交易日（${tradeDate}）的历史图形响应不完整，已保留当前图形。`,
          );
        }
        return;
      }
      const snapshot = inspected.snapshot;
      // Install the historical projection on Live first, then leave Replay so
      // the visible switch lands on the prepared day chart (not a stale Live).
      setWorkbench((current) => selectWorkbenchSecurity(current, identity));
      setQuery(identity.code);
      liveProjectionController.current?.replace(
        createChartProjection(snapshot, {
          service_generation: serviceGeneration.current,
          session_id: session.session_id,
          revision: session.revision,
        }),
      );
      if (modeRef.current === WorkbenchMode.REPLAY) {
        selectMode(WorkbenchMode.LIVE);
      }
      setLoading(false);
    } catch (error) {
      if (!navigationRequests.current.isCurrent(requestSequence)) return;
      const appError = clientError(error, "historical_chart");
      setLoading(false);
      setDayChartNotice(
        `该交易日（${tradeDate}）的历史图形加载失败：${appError.message}`,
      );
    }
  }

  async function retryLiveOrService() {
    const failure = activeFailure;
    const background = backgroundError;
    setBackgroundError(null);
    setActiveFailure(null);
    if (background?.affected_capability === "preferences") {
      if (!preferencesHydrated) {
        setPreferenceHydrationAttempt((current) => current + 1);
        return;
      }
      const symbol = workbench.security?.symbol;
      if (!symbol) return;
      try {
        const response = await window.stockpilot.saveLastSymbol(
          appRequest("save_last_symbol", null, { symbol }),
        );
        const error = applicationErrorFrom(response);
        if (error) throw error;
      } catch (error) {
        setBackgroundError(clientError(error, "preferences"));
      }
      return;
    }
    // Replay-owned failures must never enter retry_live. Ownership is based on
    // the Replay event/command channel (source: "replay"), not only
    // affected_capability === "replay".
    const replayOwned =
      isReplayOwnedError(background) || isReplayOwnedError(failure?.error);
    if (replayOwned) {
      replaySessionController.current?.clearCursor();
      replaySessionController.current?.setResumeAfterSeek(false);
      replaySessionController.current?.setBusy(false);
      replaySessionController.current?.setPlaybackPending(false);
      const replayError = background ?? failure?.error;
      if (replayError?.affected_capability === "service") {
        setLoading(true);
        try {
          await window.stockpilot.retryService();
        } catch (error) {
          setBackgroundError(
            asReplayOwnedError(clientError(error, "service")),
          );
        } finally {
          setLoading(false);
        }
      }
      return;
    }
    setLoading(true);
    try {
      if (
        status.state === "failed" ||
        status.state === "disconnected" ||
        failure?.retry === "service"
      ) {
        await window.stockpilot.retryService();
        setLoading(false);
        return;
      }
      if (failure?.retry === "security" && failure.security) {
        await performSecuritySelection(failure.security);
        return;
      }
      if (!projection.sessionId) {
        setLoading(false);
        return;
      }
      const response = await window.stockpilot.retryLive(
        appRequest("retry_live", projection.sessionId, {}),
      );
      const error = applicationErrorFrom(response);
      if (error) throw error;
      const operationId = responseOperationId(response);
      const replacementSessionId = responseSessionId(response);
      const retainedSnapshot = liveProjectionController.current!.projection
        .snapshot;
      liveProjectionController.current!.beginSession(
        retainedSnapshot,
        status.service_generation,
        replacementSessionId,
      );
      if (operationId) {
        activeOperations.current.set(operationId, {
          retry: "live",
          serviceGeneration: status.service_generation,
          sessionId: replacementSessionId,
        });
      }
    } catch (error) {
      setLoading(false);
      setBackgroundError(clientError(error, "live"));
    }
  }

  async function beginReplay() {
    if (!window.stockpilot || !workbench.security || !replayDate) return;
    const replay = replaySessionController.current;
    if (!replay) return;
    replay.setServiceGeneration(serviceGeneration.current);
    replay.setLoading(true);
    replay.setBusy(false);
    replay.clearCursor();
    replay.setResumeAfterSeek(false);
    setBackgroundError(null);
    try {
      const response = await window.stockpilot.beginReplay({
        schema_version: "t0_replay_v2",
        request_id: requestId("begin-replay"),
        symbol: workbench.security.symbol,
        trade_date: replayDate,
      });
      const error = applicationErrorFrom(response);
      if (error) throw error;
      const sessionId = responseSessionId(response);
      if (!sessionId) throw new TypeError("回放响应缺少 session_id");
      replay.beginSession(sessionId, responseOperationId(response));
    } catch (error) {
      replay.failLoadOperation();
      setBackgroundError(clientError(error, "replay"));
    }
  }

  async function setReplayPlayback(playing: boolean) {
    if (!window.stockpilot || !replayFacts) return;
    replaySessionController.current?.setPlaybackPending(true);
    try {
      const response = await window.stockpilot.setReplayPlayback({
        schema_version: "t0_replay_v2",
        request_id: requestId(playing ? "play-replay" : "pause-replay"),
        session_id: replayFacts.sessionId,
        playing,
      });
      const error = applicationErrorFrom(response);
      if (error) throw error;
    } catch (error) {
      setBackgroundError(clientError(error, "replay"));
    } finally {
      replaySessionController.current?.setPlaybackPending(false);
    }
  }

  async function setReplaySpeed(playbackSpeed: number) {
    if (
      !window.stockpilot ||
      !replayFacts ||
      !REPLAY_SPEEDS.includes(playbackSpeed as 1 | 2 | 5 | 10)
    ) {
      return;
    }
    try {
      const response = await window.stockpilot.setReplaySpeed({
        schema_version: "t0_replay_v2",
        request_id: requestId("set-replay-speed"),
        session_id: replayFacts.sessionId,
        playback_speed: playbackSpeed,
      });
      const error = applicationErrorFrom(response);
      if (error) throw error;
    } catch (error) {
      setBackgroundError(clientError(error, "replay"));
    }
  }

  function adoptReplayCursorResponse(operationId: string | null) {
    const replay = replaySessionController.current;
    if (!replay) return;
    const adopted = replay.adoptCursorOperation(operationId);
    if (adopted.status === "no_operation") {
      if (replay.takeResumeAfterSeek()) {
        void setReplayPlayback(true);
      }
      return;
    }
    if (adopted.status === "already_settled") {
      if (adopted.early === "completed" && replay.takeResumeAfterSeek()) {
        void setReplayPlayback(true);
      }
    }
  }

  async function stepReplay() {
    if (!window.stockpilot || !replayFacts) return;
    replaySessionController.current?.setBusy(true);
    try {
      const response = await window.stockpilot.stepReplay({
        schema_version: "t0_replay_v2",
        request_id: requestId("step-replay"),
        session_id: replayFacts.sessionId,
      });
      const error = applicationErrorFrom(response);
      if (error) throw error;
      adoptReplayCursorResponse(responseOperationId(response));
    } catch (error) {
      replaySessionController.current?.clearCursor();
      replaySessionController.current?.setBusy(false);
      setBackgroundError(asReplayOwnedError(clientError(error, "replay")));
    }
  }

  async function seekReplay(targetTime: string) {
    if (!window.stockpilot || !replayFacts) return;
    replaySessionController.current?.setBusy(true);
    try {
      const response = await window.stockpilot.seekReplay({
        schema_version: "t0_replay_v2",
        request_id: requestId("seek-replay"),
        session_id: replayFacts.sessionId,
        target_time: targetTime,
      });
      const error = applicationErrorFrom(response);
      if (error) throw error;
      adoptReplayCursorResponse(responseOperationId(response));
    } catch (error) {
      replaySessionController.current?.clearCursor();
      replaySessionController.current?.setResumeAfterSeek(false);
      replaySessionController.current?.setBusy(false);
      setBackgroundError(asReplayOwnedError(clientError(error, "replay")));
    }
  }

  function selectMode(mode: "live" | "replay") {
    if (mode === workbench.mode) return;
    modeRef.current = mode;
    const live = liveProjectionController.current;
    const replay = replaySessionController.current;
    if (mode === WorkbenchMode.REPLAY) {
      replay?.enterMode(live?.projection);
      setLoading(false);
      if (activeFailure) {
        const presentation = liveOperationFailurePresentation(
          WorkbenchMode.REPLAY,
          activeFailure.error,
        );
        setActiveFailure(null);
        setBackgroundError(presentation.error);
      }
      updateWorkbenchFromUser((current) =>
        selectWorkbenchMode(current, WorkbenchMode.REPLAY),
      );
      return;
    }
    const sessionId = replay?.exitMode() ?? null;
    updateWorkbenchFromUser((current) =>
      selectWorkbenchMode(current, WorkbenchMode.LIVE),
    );
    setReplayDate("");
    if (window.stockpilot && sessionId) {
      void window.stockpilot
        .endReplay({
          schema_version: "t0_replay_v2",
          request_id: requestId("end-replay"),
          session_id: sessionId,
        })
        .then((response) => {
          const error = applicationErrorFrom(response);
          if (error) setBackgroundError(error);
        })
        .catch((error) =>
          setBackgroundError(clientError(error, "replay")),
        );
    }
  }

  // Issue #163: trade scope follows the *visible* chart projection session,
  // not the search-box selection. In Replay the user may pick another code
  // before beginning a new session; workbench.security then diverges from the
  // still-visible Replay projection.
  const visibleSymbol = snapshot.session?.symbol ?? null;
  const visibleTradeDate = snapshot.session?.trade_date ?? localToday();
  const visibleSecurity =
    workbench.security != null &&
    workbench.security.symbol === visibleSymbol
      ? workbench.security
      : null;
  const isTradableSecurity =
    visibleSecurity != null && visibleSecurity.instrument_type !== "index";
  const isKnownNonTradableVisible =
    visibleSecurity != null && visibleSecurity.instrument_type === "index";
  const serviceReady =
    status.state === "connected" || status.state === "ready";

  tradeScopeRef.current = {
    symbol: visibleSymbol,
    tradeDate: visibleTradeDate,
  };

  // Issue #163: list_trades once when the visible symbol+trade_date is ready
  // (Live + Replay tradable only). Play/step/seek must not re-list — only
  // filter by cursor. Re-list on matching scoped trades_changed (via event
  // handler), generation change, or reconnect (serviceReady / generation deps).
  useEffect(() => {
    if (!visibleSymbol || isKnownNonTradableVisible) {
      setRealTrades({
        trades: [],
        tradeRevision: -1,
        serviceGeneration: realTradesRef.current.serviceGeneration,
        loadedScope: null,
      });
      return;
    }
    if (!tradeClient || !serviceReady || !isTradableSecurity) {
      // Selection diverged from the visible chart (e.g. Replay pending a new
      // begin): keep existing markers for the visible session; do not list
      // the newly selected code onto the old chart.
      return;
    }
    let cancelled = false;
    setRealTrades((current) => ({
      trades: [],
      tradeRevision: current.tradeRevision,
      serviceGeneration: current.serviceGeneration,
      loadedScope: null,
    }));
    void tradeClient
      .listTrades({ symbol: visibleSymbol, tradeDate: visibleTradeDate })
      .catch(() => {
        // Keep empty list; trades_changed or retry will recover.
        if (cancelled) return;
      });
    return () => {
      cancelled = true;
    };
  }, [
    tradeClient,
    serviceReady,
    isTradableSecurity,
    isKnownNonTradableVisible,
    visibleSymbol,
    visibleTradeDate,
    status.service_generation,
  ]);

  const chartTrades = useMemo(() => {
    if (!visibleSymbol || isKnownNonTradableVisible) return [];
    const scoped = realTrades.trades.filter(
      (trade) =>
        trade.symbol === visibleSymbol &&
        tradeDateOf(trade.executed_at) === visibleTradeDate,
    );
    if (workbench.mode === WorkbenchMode.REPLAY) {
      return filterTradesByReplayCursor(
        scoped,
        replayFacts?.currentTime ?? null,
      ) as TradeRecord[];
    }
    return scoped;
  }, [
    realTrades.trades,
    visibleSymbol,
    visibleTradeDate,
    workbench.mode,
    isKnownNonTradableVisible,
    replayFacts?.currentTime,
  ]);

  const fiveMinuteModelResult = useMemo(
    () =>
      tryCreateChartGroupModel(
        snapshot,
        ChartGroupKind.FIVE_MINUTE,
        workbench.layers,
      ),
    [snapshot, workbench.layers],
  );
  const intradayModelResult = useMemo(
    () => tryCreateChartGroupModel(snapshot, ChartGroupKind.ONE_MINUTE),
    [snapshot],
  );
  const fiveMinuteModel =
    fiveMinuteModelResult.ok
      ? fiveMinuteModelResult.model
      : (lastGoodChartModels.current?.fiveMinute ??
        createChartGroupModel(
          emptyChartSnapshot,
          ChartGroupKind.FIVE_MINUTE,
        ));
  const intradayModel =
    intradayModelResult.ok
      ? intradayModelResult.model
      : (lastGoodChartModels.current?.intraday ??
        createChartGroupModel(emptyChartSnapshot, ChartGroupKind.ONE_MINUTE));

  const fiveMinuteTradeMarkers = useMemo(() => {
    if (fiveMinuteModel.kind !== ChartGroupKind.FIVE_MINUTE) return [];
    const allowedTimes = new Set(Object.values(fiveMinuteModel.timeByTimestamp));
    return projectTradeMarkers(chartTrades, { allowedTimes });
  }, [chartTrades, fiveMinuteModel]);

  useEffect(() => {
    if (fiveMinuteModelResult.ok && intradayModelResult.ok) {
      lastGoodChartModels.current = {
        fiveMinute: fiveMinuteModelResult.model,
        intraday: intradayModelResult.model,
      };
      setBackgroundError((current) =>
        current?.error_code === "chart_contract_failed" ||
        current?.error_code === "chart_envelope_failed"
          ? null
          : current,
      );
      return;
    }
    const failure = !fiveMinuteModelResult.ok
      ? fiveMinuteModelResult.error
      : !intradayModelResult.ok
        ? intradayModelResult.error
        : null;
    if (failure == null) return;
    setBackgroundError(chartContractApplicationError(failure));
    if (workbench.mode === WorkbenchMode.LIVE) {
      liveProjectionController.current?.requestRebaseline();
    }
  }, [
    fiveMinuteModelResult,
    intradayModelResult,
    workbench.mode,
  ]);

  // 视口状态镜像到 workbench.chartViews（UI 规格 §12：在 React 状态层保存可见范围）。
  // 保存 {range, followState} 快照；controller 节流上报，避免高频 setState。React 是
  // 运行时权威，组件重建后据此恢复，不依赖图表实例未被卸载。
  const rememberChartView = (
    group: "fiveMinute" | "intraday",
    snapshot: ChartViewportSnapshot | null,
  ) => {
    setWorkbench((current) => {
      const existing = current.chartViews[group];
      if (
        snapshot &&
        existing &&
        existing.followState === snapshot.followState &&
        existing.range.from === snapshot.range.from &&
        existing.range.to === snapshot.range.to
      ) {
        return current;
      }
      if (!snapshot && !existing) {
        return current;
      }
      return {
        ...current,
        chartViews: { ...current.chartViews, [group]: snapshot },
      };
    });
  };
  // 同股票数据集 identity 替换时丢弃旧手工范围，避免 key 重建后错误恢复（Issue #148）。
  const chartDatasetIdentity = replayFacts ? null : projection.sessionId;
  const chartDatasetIdentityRef = useRef(chartDatasetIdentity);
  const datasetIdentityJustReplaced =
    chartDatasetIdentityRef.current != null &&
    chartDatasetIdentity != null &&
    chartDatasetIdentityRef.current !== chartDatasetIdentity;
  useEffect(() => {
    const previous = chartDatasetIdentityRef.current;
    chartDatasetIdentityRef.current = chartDatasetIdentity;
    if (
      previous == null ||
      chartDatasetIdentity == null ||
      previous === chartDatasetIdentity
    ) {
      return;
    }
    setWorkbench((current) => ({
      ...current,
      chartViews: { fiveMinute: null, intraday: null },
    }));
  }, [chartDatasetIdentity]);
  const fiveMinuteInitialViewport =
    replayFacts || datasetIdentityJustReplaced
      ? null
      : workbench.chartViews.fiveMinute;
  const intradayInitialViewport =
    replayFacts || datasetIdentityJustReplaced
      ? null
      : workbench.chartViews.intraday;
  const layoutMode = workbenchLayoutMode(workbench);
  const dailyBars = latestDailyBars(snapshot);

  const replayMode = workbench.mode === WorkbenchMode.REPLAY;
  // Historical day charts share Live mode but must stay read-only: CRUD belongs
  // to the Live drawer / history dialog, not the restored day view (#163).
  const historicalChartVisible =
    snapshot.session?.session_type === "historical";
  const tradeDrawerReadOnly = replayMode || historicalChartVisible;
  const fiveMinuteFallback =
    replayMode && replayFacts?.granularity === "five_minute";

  return (
    <main
      data-testid="shell"
      className={[
        "shell",
        backgroundError || activeFailure ? "has-feedback" : "",
        replayMode ? "replay-mode" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <WorkbenchToolbar
        query={query}
        workbench={workbench}
        searching={searching}
        searchMessage={searchMessage}
        suggestions={suggestions}
        onQuery={setQuery}
        onSelect={(security) => void performSecuritySelection(security)}
        onMode={selectMode}
      />

      {(activeFailure || backgroundError) && (
        <section
          className="feedback-banner"
          role={activeFailure ? "alert" : "status"}
        >
          <span>
            {(activeFailure?.error ?? backgroundError)?.message}
          </span>
          {shouldShowFeedbackRetry(
            activeFailure?.error ?? backgroundError,
          ) && (
            <button type="button" onClick={() => void retryLiveOrService()}>
              重试
            </button>
          )}
          <button
            type="button"
            aria-label="关闭提示"
            onClick={() => {
              setActiveFailure(null);
              setBackgroundError(null);
            }}
          >
            ×
          </button>
        </section>
      )}

      {tradeFailures.length > 0 && (
        <section
          className="feedback-banner trade-feedback"
          role="status"
          aria-label="成交操作提示"
        >
          {tradeFailures.map((failure) => (
            <div className="trade-feedback-item" key={failure.failureId}>
              <span>{failure.message}</span>
              {failure.retry && (
                <button
                  type="button"
                  onClick={() => {
                    const retry = failure.retry;
                    // Dismiss before retrying so the banner doesn't persist
                    // while the retry is in flight; a fresh failure is tracked
                    // by the controller if it fails again.
                    tradeOpController.current?.dismissFailure(failure.failureId);
                    if (retry) void retry();
                  }}
                >
                  重试
                </button>
              )}
              <button
                type="button"
                aria-label="关闭提示"
                onClick={() =>
                  tradeOpController.current?.dismissFailure(failure.failureId)
                }
              >
                ×
              </button>
            </div>
          ))}
        </section>
      )}

      <section
        className="workspace"
        data-testid="workbench"
        data-chart-split={workbench.layout.chartSplit}
        data-show-intraday={workbench.layout.showIntraday}
        aria-label="T+0 三栏三行工作台"
      >
        <article
          className="chart-group five-minute-group"
          data-testid="five-minute-group"
          aria-label="5 分钟图表组"
        >
          <ChartGroup
            key={`five-${workbench.security?.symbol ?? "fixture"}-${
              replayFacts?.sessionId ?? projection.sessionId ?? "live"
            }`}
            model={fiveMinuteModel}
            tradeMarkers={fiveMinuteTradeMarkers}
            appendFollowPolicy={
              replayFacts
                ? "preserve"
                : "force-follow-latest"
            }
            datasetIdentity={chartDatasetIdentity}
            initialViewport={fiveMinuteInitialViewport}
            onViewportChange={
              replayFacts
                ? undefined
                : (snapshot) => rememberChartView("fiveMinute", snapshot)
            }
            priceHeader={
              <div className="panel-heading">
                <h1>5 分钟</h1>
                <div className="heading-actions">
                  <LayerSwitcher
                    state={workbench}
                    onToggle={(layer) =>
                      updateWorkbenchFromUser((current) =>
                        toggleWorkbenchLayer(current, layer),
                      )
                    }
                  />
                  <LayoutSwitcher
                    mode={layoutMode}
                    onSelect={(mode) =>
                      updateWorkbenchFromUser((current) =>
                        selectWorkbenchLayout(current, mode),
                      )
                    }
                  />
                </div>
              </div>
            }
          />
        </article>

        <article
          className="chart-group intraday-group"
          data-testid="intraday-group"
          aria-label="分时图表组"
          hidden={!workbench.layout.showIntraday}
        >
          {fiveMinuteFallback ? (
            <div className="intraday-unavailable" role="status">
              无 1 分钟数据
            </div>
          ) : (
            <ChartGroup
              key={`intra-${workbench.security?.symbol ?? "fixture"}-${
                replayFacts?.sessionId ?? projection.sessionId ?? "live"
              }`}
              model={intradayModel}
              initialViewport={intradayInitialViewport}
              onViewportChange={
                replayFacts
                  ? undefined
                  : (snapshot) => rememberChartView("intraday", snapshot)
              }
              priceHeader={
                <div className="panel-heading">
                  <h2>分时</h2>
                </div>
              }
            />
          )}
        </article>

        <MarketSidebar
          bars={dailyBars}
          quote={snapshot.market.quote}
          status={status}
        />

        {loading && (
          <div className="loading-indicator" role="status">
            {restoreMessage ?? "正在加载…"}
          </div>
        )}
      </section>

      {replayMode && (
        <ReplayControls
          tradeDate={replayDate}
          securitySelected={Boolean(workbench.security)}
          loading={replayCtrl.loading}
          busy={replayCtrl.busy}
          playbackPending={replayCtrl.playbackPending}
          facts={replayFacts}
          onTradeDate={setReplayDate}
          onBegin={() => void beginReplay()}
          onPlayback={(playing) => void setReplayPlayback(playing)}
          onStep={() => void stepReplay()}
          onSpeed={(speed) => void setReplaySpeed(speed)}
          onSeek={(targetTime, options) => {
            replayCtrl.setResumeAfterSeek(Boolean(options?.resumeAfter));
            void seekReplay(targetTime);
          }}
        />
      )}

      {isTradableSecurity && (
        <TradeDrawer
          security={workbench.security}
          tradeClient={tradeClient}
          feePlanClient={tradeDrawerReadOnly ? null : feePlanClient}
          feeAdvisor={feeAdvisor}
          serviceReady={serviceReady}
          subscribeAppEvent={subscribeAppEvent}
          serviceGeneration={status.service_generation}
          tradeOpController={tradeOpController.current as TradeOperationController}
          onEnterDayChart={handleEnterDayChart}
          resolveSecurity={resolveSecurity}
          readOnly={tradeDrawerReadOnly}
          dayTrades={tradeDrawerReadOnly ? chartTrades : undefined}
          tradeDate={tradeDrawerReadOnly ? visibleTradeDate : undefined}
        />
      )}

      {dayChartNotice && (
        <div className="inline-error" role="status" style={{ margin: "8px 12px" }}>
          <span>{dayChartNotice}</span>
          <button type="button" onClick={() => setDayChartNotice(null)}>
            知道了
          </button>
        </div>
      )}

    </main>
  );

  function updateWorkbenchFromUser(
    update: (current: WorkbenchState) => WorkbenchState,
  ) {
    userModifiedPreferences.current = true;
    setWorkbench(update);
  }
}

function ReplayControls({
  tradeDate,
  securitySelected,
  loading,
  busy,
  playbackPending,
  facts,
  onTradeDate,
  onBegin,
  onPlayback,
  onStep,
  onSpeed,
  onSeek,
}: {
  tradeDate: string;
  securitySelected: boolean;
  loading: boolean;
  busy: boolean;
  playbackPending: boolean;
  facts: ReplayFacts | null;
  onTradeDate: (value: string) => void;
  onBegin: () => void;
  onPlayback: (playing: boolean) => void;
  onStep: () => void;
  onSpeed: (speed: number) => void;
  onSeek: (
    targetTime: string,
    options?: { resumeAfter?: boolean },
  ) => void;
}) {
  const controls = deriveReplayControls(facts, { busy });
  const [draftProgress, setDraftProgress] = useState<number | null>(null);
  const dragging = useRef(false);
  const resumeAfterSeek = useRef(false);
  const progress = draftProgress ?? facts?.currentValue ?? 0;
  const shownTime = facts
    ? marketClockLabel(marketTimeFromValue(progress))
    : "--:--";

  useEffect(() => {
    setDraftProgress(null);
  }, [facts?.currentValue, facts?.sessionId]);

  if (!facts) {
    return (
      <section
        className="replay-controls replay-setup"
        data-testid="replay-controls"
        aria-label="回放控制面板"
      >
        <label htmlFor="replay-date">回放日期</label>
        <input
          id="replay-date"
          type="date"
          value={tradeDate}
          max={localDateToday()}
          disabled={loading}
          onChange={(event) => onTradeDate(event.target.value)}
        />
        <button
          type="button"
          className="replay-primary"
          disabled={!securitySelected || !tradeDate || loading}
          onClick={onBegin}
        >
          {loading ? "正在准备回放…" : "开始回放"}
        </button>
        {!securitySelected && (
          <span className="replay-hint">请先选择股票</span>
        )}
      </section>
    );
  }

  function commitSeek() {
    dragging.current = false;
    if (draftProgress === null || !controls.canSeek) {
      resumeAfterSeek.current = false;
      return;
    }
    const shouldResume = resumeAfterSeek.current;
    resumeAfterSeek.current = false;
    onSeek(marketTimeFromValue(draftProgress), { resumeAfter: shouldResume });
  }

  return (
    <section
      className="replay-controls replay-active"
      data-testid="replay-controls"
      aria-label="回放控制面板"
      aria-busy={busy}
    >
      <button
        type="button"
        className="replay-playback"
        aria-label={controls.playing ? "暂停回放" : "播放回放"}
        disabled={!controls.canTogglePlayback || playbackPending}
        onClick={() => onPlayback(!controls.playing)}
      >
        <span aria-hidden="true">{controls.playing ? "Ⅱ" : "▶"}</span>
        {controls.playing ? "暂停" : "播放"}
      </button>
      <span className="replay-boundary">
        {marketClockLabel(facts.startTime)}
      </span>
      <div className="replay-progress">
        <output
          style={{
            left: `${progressPercent(
              progress,
              facts.startValue,
              facts.endValue,
            )}%`,
          }}
        >
          {shownTime}
        </output>
        <input
          type="range"
          aria-label="回放进度"
          min={facts.startValue}
          max={facts.endValue}
          step={facts.granularity === "five_minute" ? 300_000 : 60_000}
          value={progress}
          disabled={!controls.canSeek}
          onPointerDown={() => {
            dragging.current = true;
            if (controls.playing) {
              resumeAfterSeek.current = true;
              onPlayback(false);
            } else {
              resumeAfterSeek.current = false;
            }
          }}
          onChange={(event) => setDraftProgress(Number(event.target.value))}
          onPointerUp={commitSeek}
          onKeyUp={commitSeek}
          onBlur={() => {
            if (dragging.current) commitSeek();
          }}
        />
      </div>
      <span className="replay-boundary">
        {marketClockLabel(facts.endTime)}
      </span>
      <span className="replay-granularity">{controls.granularityLabel}</span>
      <button
        type="button"
        disabled={!controls.canStep}
        onClick={onStep}
      >
        {controls.stepLabel}
      </button>
      <label className="replay-speed">
        <span className="sr-only">回放倍速</span>
        <select
          aria-label="回放倍速"
          value={facts.playbackSpeed}
          disabled={!controls.canChangeSpeed}
          onChange={(event) => onSpeed(Number(event.target.value))}
        >
          {REPLAY_SPEEDS.map((speed) => (
            <option key={speed} value={speed}>
              {speed}×
            </option>
          ))}
        </select>
      </label>
    </section>
  );
}

function WorkbenchToolbar({
  query,
  workbench,
  searching,
  searchMessage,
  suggestions,
  onQuery,
  onSelect,
  onMode,
}: {
  query: string;
  workbench: WorkbenchState;
  searching: boolean;
  searchMessage: string;
  suggestions: SecurityIdentity[];
  onQuery: (value: string) => void;
  onSelect: (security: SecurityIdentity) => void;
  onMode: (mode: "live" | "replay") => void;
}) {
  const [searchState, dispatchSearch] = useReducer(
    securitySearchReducer,
    initialSecuritySearchState,
  );
  const { activeIndex, dismissed } = searchState;
  const resultsRef = useRef<HTMLDivElement>(null);

  // Reset keyboard cursor and dismissed flag whenever the result set or query
  // changes so the user always starts fresh.
  useEffect(() => {
    dispatchSearch({ type: "reset-cursor" });
  }, [suggestions]);

  useEffect(() => {
    dispatchSearch({ type: "query-change" });
  }, [query]);

  // Keep the highlighted option scrolled into view during keyboard navigation.
  useEffect(() => {
    if (activeIndex < 0 || !resultsRef.current) return;
    const active = resultsRef.current.querySelector<HTMLElement>(
      `[data-index="${activeIndex}"]`,
    );
    active?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  const showResults =
    !dismissed &&
    (searching || Boolean(searchMessage) || suggestions.length > 0);

  function selectSuggestion(security: SecurityIdentity) {
    dispatchSearch({ type: "select" });
    onSelect(security);
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      if (suggestions.length === 0) return;
      event.preventDefault();
      dispatchSearch({ type: "arrow-down", count: suggestions.length });
    } else if (event.key === "ArrowUp") {
      if (suggestions.length === 0) return;
      event.preventDefault();
      dispatchSearch({ type: "arrow-up", count: suggestions.length });
    } else if (event.key === "Enter") {
      if (dismissed || suggestions.length === 0) return;
      event.preventDefault();
      const target = activeIndex >= 0 ? activeIndex : 0;
      selectSuggestion(suggestions[target]);
    } else if (event.key === "Escape") {
      if (!showResults) return;
      event.preventDefault();
      dispatchSearch({ type: "escape", visible: showResults });
    }
  }

  return (
    <header className="toolbar" data-testid="toolbar">
      <div className="security-picker">
        <label className="sr-only" htmlFor="security-search">
          股票搜索
        </label>
        <input
          id="security-search"
          value={query}
          placeholder="代码/名称/拼音首字母"
          autoComplete="off"
          aria-autocomplete="list"
          aria-expanded={showResults}
          aria-controls="security-results"
          aria-activedescendant={
            activeIndex >= 0
              ? `security-option-${activeIndex}`
              : undefined
          }
          onChange={(event) => onQuery(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        {showResults && (
          <div
            id="security-results"
            className="search-results"
            role="listbox"
            ref={resultsRef}
          >
            {searching && <div className="search-hint">正在搜索…</div>}
            {!searching &&
              suggestions.map((security, index) => (
                <button
                  type="button"
                  role="option"
                  id={`security-option-${index}`}
                  data-index={index}
                  key={security.symbol}
                  aria-selected={index === activeIndex}
                  className={index === activeIndex ? "is-active" : undefined}
                  onClick={() => selectSuggestion(security)}
                  onMouseEnter={() =>
                    dispatchSearch({ type: "mouse-enter", index })
                  }
                >
                  <span>{security.code}</span>
                  <strong>{security.name}</strong>
                  <small>{securityCategoryLabel(security)}</small>
                </button>
              ))}
            {!searching && searchMessage && (
              <div className="search-hint">{searchMessage}</div>
            )}
          </div>
        )}
        <span className="security-name">{workbench.security?.name ?? ""}</span>
      </div>
      <div
        className="mode-switcher"
        data-testid="mode-switcher"
        aria-label="工作台模式"
      >
        <button
          type="button"
          data-testid="mode-live"
          aria-pressed={workbench.mode === WorkbenchMode.LIVE}
          onClick={() => onMode(WorkbenchMode.LIVE)}
        >
          实盘
        </button>
        <button
          type="button"
          data-testid="mode-replay"
          aria-pressed={workbench.mode === WorkbenchMode.REPLAY}
          onClick={() => onMode(WorkbenchMode.REPLAY)}
        >
          回放
        </button>
      </div>
    </header>
  );
}

function LayerSwitcher({
  state,
  onToggle,
}: {
  state: WorkbenchState;
  onToggle: (layer: keyof WorkbenchState["layers"]) => void;
}) {
  const layers: Array<[keyof WorkbenchState["layers"], string]> = [
    [WorkbenchLayer.MA5, "MA5"],
    [WorkbenchLayer.MA10, "MA10"],
    [WorkbenchLayer.MA20, "MA20"],
    [WorkbenchLayer.MA30, "MA30"],
    [WorkbenchLayer.MA60, "MA60"],
    [WorkbenchLayer.STROKES, "笔"],
    [WorkbenchLayer.PIVOT_ZONES, "笔中枢"],
  ];
  return (
    <div
      className="layer-switcher"
      data-testid="layer-switcher"
      aria-label="图层开关"
    >
      {layers.map(([layer, label]) => (
        <button
          type="button"
          key={layer}
          data-layer={layer}
          data-testid={`layer-${layer}`}
          aria-pressed={state.layers[layer]}
          onClick={() => onToggle(layer)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function LayoutSwitcher({
  mode,
  onSelect,
}: {
  mode: WorkbenchLayoutModeValue;
  onSelect: (mode: WorkbenchLayoutModeValue) => void;
}) {
  return (
    <div
      className="layout-switcher"
      data-testid="layout-switcher"
      aria-label="工作台布局"
    >
      <LayoutButton
        active={mode === WorkbenchLayoutMode.MAIN_PRIORITY}
        testId="layout-main-priority"
        label="64 / 36"
        onClick={() => onSelect(WorkbenchLayoutMode.MAIN_PRIORITY)}
      />
      <LayoutButton
        active={mode === WorkbenchLayoutMode.EQUAL}
        testId="layout-equal"
        label="50 / 50"
        onClick={() => onSelect(WorkbenchLayoutMode.EQUAL)}
      />
      <LayoutButton
        active={mode === WorkbenchLayoutMode.HIDE_INTRADAY}
        testId="layout-hide-intraday"
        label="隐藏分时"
        onClick={() => onSelect(WorkbenchLayoutMode.HIDE_INTRADAY)}
      />
    </div>
  );
}

function LayoutButton({
  active,
  testId,
  label,
  onClick,
}: {
  active: boolean;
  testId: string;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-pressed={active}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

function MarketSidebar({
  bars,
  quote,
  status,
}: {
  bars: MarketBar[];
  quote: unknown;
  status: ServiceStatus;
}) {
  return (
    <aside
      className="market-sidebar"
      data-testid="market-sidebar"
      aria-label="行情栏"
    >
      <section className="daily-chart">
        <h2>日 K</h2>
        <DailyMiniChart bars={bars} />
      </section>
      <section className="quote-panel">
        <h2>行情数据</h2>
        <dl>
          {quoteRows(quote).map(([label, value]) => (
            <div
              key={label}
              className={label === "委比" ? "secondary-quote" : undefined}
            >
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <p className="quote-data-cutoff">{quoteDataCutoffText(quote)}</p>
      </section>
      <section className="service-card" aria-label="本地服务状态">
        <span className={`status status-${status.state}`}>{status.state}</span>
        <span className="service-message">{status.message}</span>
      </section>
    </aside>
  );
}

function DailyMiniChart({ bars }: { bars: MarketBar[] }) {
  if (bars.length === 0) {
    return <div className="chart-placeholder" aria-label="日 K 图" />;
  }
  const width = 240;
  const height = 170;
  const low = Math.min(...bars.map((bar) => bar.low));
  const high = Math.max(...bars.map((bar) => bar.high));
  const range = high - low || 1;
  const step = width / bars.length;
  const y = (value: number) => 6 + ((high - value) / range) * (height - 12);
  return (
    <svg
      className="daily-mini-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`最近 ${bars.length} 个交易日日 K`}
    >
      {bars.map((bar, index) => {
        const x = index * step + step / 2;
        const rising = bar.close >= bar.open;
        const color = rising ? "#ef5350" : "#26a69a";
        const bodyTop = Math.min(y(bar.open), y(bar.close));
        const bodyHeight = Math.max(1, Math.abs(y(bar.open) - y(bar.close)));
        return (
          <g key={bar.timestamp}>
            <line x1={x} x2={x} y1={y(bar.high)} y2={y(bar.low)} stroke={color} />
            <rect
              x={x - Math.max(1, step * 0.28)}
              y={bodyTop}
              width={Math.max(2, step * 0.56)}
              height={bodyHeight}
              fill={color}
            />
          </g>
        );
      })}
    </svg>
  );
}

function appRequest(command: string, sessionId: string | null, payload: object) {
  return {
    schema_version: "t0_app_v2",
    request_id: requestId(command),
    command,
    session_id: sessionId,
    payload,
  };
}

function requestId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function localDateToday() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function progressPercent(value: number, start: number, end: number) {
  if (end <= start) return 0;
  return Math.min(100, Math.max(0, ((value - start) / (end - start)) * 100));
}

function responseOperationId(response: unknown) {
  return response && typeof response === "object" &&
    typeof (response as { operation_id?: unknown }).operation_id === "string"
    ? (response as { operation_id: string }).operation_id
    : null;
}

function responseSessionId(response: unknown) {
  if (!response || typeof response !== "object") return null;
  const result = response as {
    session_id?: unknown;
    data?: { session_id?: unknown };
  };
  const candidate = result.data?.session_id ?? result.session_id;
  return typeof candidate === "string" && candidate.length > 0
    ? candidate
    : null;
}

function preferenceWarningFromResponse(
  response: unknown,
): ApplicationError | null {
  if (!response || typeof response !== "object") return null;
  const warning = (
    response as { data?: { preference_warning?: unknown } }
  ).data?.preference_warning;
  return applicationErrorFrom(warning);
}

function preferencesFromResponse(response: unknown) {
  if (!response || typeof response !== "object") return null;
  const data = (response as { data?: unknown; snapshot?: unknown }).data ?? response;
  if (!data || typeof data !== "object") return null;
  const candidate =
    (data as { preferences?: unknown }).preferences ??
    (data as { snapshot?: { preferences?: unknown } }).snapshot?.preferences;
  return candidate && typeof candidate === "object"
    ? (candidate as Parameters<typeof applyWorkbenchPreferences>[1])
    : null;
}

function serviceStatusError(status: ServiceStatus): ApplicationError {
  return {
    error_code: "service_unavailable",
    message: status.message || "本地服务暂时不可用",
    retryable: true,
    affected_capability: "service",
  };
}

function clientError(error: unknown, capability: string): ApplicationError {
  const known = applicationErrorFrom(error);
  if (known) {
    return capability === "replay" ? asReplayOwnedError(known) : known;
  }
  const fallback: ApplicationError = {
    error_code: "client_operation_failed",
    message:
      capability === "preferences"
        ? "偏好暂时无法保存，当前设置仍会继续生效"
        : "操作暂时未完成，请稍后重试",
    retryable: true,
    affected_capability: capability,
  };
  return capability === "replay" ? asReplayOwnedError(fallback) : fallback;
}

function shouldShowFeedbackRetry(error: ApplicationError | null | undefined) {
  if (!error?.retryable) return false;
  if (error.affected_capability === "market_calendar") return false;
  if (!isReplayOwnedError(error)) return true;
  // Replay-channel service outages may still restart the local service.
  return error.affected_capability === "service";
}

function requestReplayPlayback(
  sessionId: string | null,
  playing: boolean,
  onError: (error: ApplicationError) => void,
) {
  if (!window.stockpilot || !sessionId) return;
  void window.stockpilot
    .setReplayPlayback({
      schema_version: "t0_replay_v2",
      request_id: requestId(playing ? "play-replay" : "pause-replay"),
      session_id: sessionId,
      playing,
    })
    .then((response) => {
      const error = applicationErrorFrom(response);
      if (error) onError(asReplayOwnedError(error));
    })
    .catch((error: unknown) => {
      onError(asReplayOwnedError(clientError(error, "replay")));
    });
}

function eventEnvelope(event: unknown) {
  if (!event || typeof event !== "object") return null;
  const envelope = event as {
    event_type?: unknown;
    operation_id?: string;
    service_generation?: unknown;
    session_id?: unknown;
    payload?: unknown;
  };
  return typeof envelope.event_type === "string"
    ? {
        event_type: envelope.event_type,
        operation_id: envelope.operation_id,
        service_generation:
          typeof envelope.service_generation === "number"
            ? envelope.service_generation
            : null,
        session_id:
          typeof envelope.session_id === "string"
            ? envelope.session_id
            : null,
        payload: envelope.payload,
      }
    : null;
}

function chartProjectionFromEvent(event: unknown): ChartProjection | null {
  if (!event || typeof event !== "object") return null;
  const envelope = event as {
    event_type?: unknown;
    service_generation?: unknown;
    session_id?: unknown;
    revision?: unknown;
    payload?: unknown;
  };
  if (
    envelope.event_type !== "workbench_snapshot" ||
    !Number.isInteger(envelope.service_generation) ||
    typeof envelope.session_id !== "string" ||
    !Number.isInteger(envelope.revision)
  ) {
    return null;
  }
  const inspected = inspectWorkbenchSnapshotCandidate(envelope.payload);
  if (!inspected.ok) return null;
  return createChartProjection(inspected.snapshot, {
    service_generation: envelope.service_generation as number,
    session_id: envelope.session_id,
    revision: envelope.revision as number,
  });
}

function isChartAppEvent(
  candidate: unknown,
): candidate is {
  event_type: string;
  service_generation?: number;
  session_id?: string | null;
  revision?: number;
  payload: unknown;
} {
  return Boolean(eventEnvelope(candidate));
}

function workbenchSnapshotFromResponse(
  response: unknown,
): WorkbenchChartSnapshot | null {
  if (!response || typeof response !== "object") return null;
  const result = response as { data?: unknown; snapshot?: unknown };
  const candidate = result.data ?? result.snapshot ?? response;
  const inspected = inspectWorkbenchSnapshotCandidate(candidate);
  return inspected.ok ? inspected.snapshot : null;
}

function projectionIdentity(projection: ChartProjection) {
  return {
    service_generation: projection.serviceGeneration ?? undefined,
    session_id: projection.sessionId,
    revision: projection.revision ?? undefined,
  };
}
