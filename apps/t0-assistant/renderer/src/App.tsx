import { useEffect, useMemo, useRef, useState } from "react";
import chartFixture from "../../contracts/fixtures/chart-groups-v1.json";
import { ChartGroup } from "./charts/ChartGroup";
import {
  ChartGroupKind,
  createChartGroupModel,
  type MarketBar,
  type WorkbenchChartSnapshot,
} from "./charts/chart-model.mjs";
import {
  applyLiveChartEvent,
  applyWorkbenchSnapshot,
  beginChartSession,
  createChartProjection,
  type ChartProjection,
} from "./charts/chart-projection.mjs";
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
  canHydratePreferences,
  createLatestRequestTracker,
  isCompleteWorkbenchSnapshot,
  latestDailyBars,
  operationMatchesEnvelope,
  quoteRows,
  securitiesFromSearchResponse,
  standardSecurityFromResponse,
  type ApplicationError,
} from "./workbench-presenter.mjs";
import { createSerialTaskQueue } from "./serial-task-queue.mjs";
import {
  REPLAY_SPEEDS,
  deriveReplayControls,
  marketClockLabel,
  marketTimeFromValue,
  replayFactsFromSnapshot,
  replayOperationMatches,
  replaySessionMatches,
  type ReplayFacts,
} from "./replay-controls.mjs";
import {
  TradeDrawer,
  createBoundTradeClient,
  createInMemoryFeePlanClient,
} from "./trading/TradeDrawer";
import { createNullFeeAdvisor } from "./trading/fee-advisor.mjs";
import { isTradeScopedError } from "./trading/app-event-ownership.mjs";
import {
  TradeOperationController,
  type TradeOperationFailure,
} from "./trading/trade-operation-controller.mjs";

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
  chan_analysis: { strokes: [], pivot_zones: [] },
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

export function App() {
  const [status, setStatus] = useState<ServiceStatus>(initialStatus);
  const [workbench, setWorkbench] = useState<WorkbenchState>(
    createWorkbenchState,
  );
  const [projection, setProjection] = useState<ChartProjection>(() =>
    createChartProjection(
      window.stockpilot
        ? emptyChartSnapshot
        : (chartFixture as unknown as WorkbenchChartSnapshot),
    ),
  );
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<SecurityIdentity[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchMessage, setSearchMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [backgroundError, setBackgroundError] =
    useState<ApplicationError | null>(null);
  const [activeFailure, setActiveFailure] = useState<ActiveFailure | null>(
    null,
  );
  const [preferencesHydrated, setPreferencesHydrated] = useState(false);
  const [preferenceHydrationAttempt, setPreferenceHydrationAttempt] =
    useState(0);
  const [replayDate, setReplayDate] = useState("");
  const [replaySnapshot, setReplaySnapshot] =
    useState<WorkbenchChartSnapshot | null>(null);
  const [replayLoading, setReplayLoading] = useState(false);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayPlaybackPending, setReplayPlaybackPending] = useState(false);
  const rebaselineRequest = useRef<string | null>(null);
  const activeOperations = useRef(new Map<string, ActiveOperation>());
  const modeRef = useRef(workbench.mode);
  const liveProjection = useRef(projection);
  const serviceGeneration = useRef(initialStatus.service_generation);
  const activeReplaySession = useRef<string | null>(null);
  const activeReplayLoadOperation = useRef<string | null>(null);
  const activeReplayCursorOperation = useRef<string | null>(null);
  const searchRequests = useRef(createLatestRequestTracker());
  const securitySelectionSequence = useRef(0);
  const preferenceHydrationInFlight = useRef(false);
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
  const snapshot = projection.snapshot;
  const replayFacts = useMemo(
    () => replayFactsFromSnapshot(replaySnapshot),
    [replaySnapshot],
  );
  // T+0 成交与收费方案客户端。成交客户端绑定冻结的 Safe Bridge（后端 CRUD
  // 尚未接入时 bridge 返回 service_unavailable，由成交 UI 主动失败重试）。
  // 收费方案暂无冻结传输契约：内存客户端仅在 fixture 模式使用，正式环境为
  // null（设置入口禁用），避免把会话内存伪装成持久化（architecture.md §5.6）。
  // 费用建议通过 FeeAdvisor 端口取得；规则不在此重新实现，故生产用 null 顾问。
  const tradeClient = useMemo(
    () => (window.stockpilot ? createBoundTradeClient(window.stockpilot) : null),
    [],
  );
  const feePlanClient = useMemo(
    () => (window.stockpilot ? null : createInMemoryFeePlanClient()),
    [],
  );
  const feeAdvisor = useMemo(() => createNullFeeAdvisor(), []);
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
    modeRef.current = workbench.mode;
  }, [workbench.mode]);

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
        activeReplaySession.current = null;
        activeReplayLoadOperation.current = null;
        activeReplayCursorOperation.current = null;
        setReplaySnapshot(null);
        setReplayDate("");
        setReplayLoading(false);
        setReplayBusy(false);
        setReplayPlaybackPending(false);
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
        const replacement = createChartProjection(emptyChartSnapshot, {
          service_generation: next.service_generation,
        });
        liveProjection.current = replacement;
        setProjection(replacement);
        return;
      }
      setProjection((current) => {
        if (
          current.serviceGeneration === null ||
          current.serviceGeneration === next.service_generation
        ) {
          return current;
        }
        activeOperations.current.clear();
        const replacement = createChartProjection(emptyChartSnapshot, {
          service_generation: next.service_generation,
        });
        liveProjection.current = replacement;
        return replacement;
      });
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
            const selected = await resolveSecurity(preferences.last_symbol);
            if (
              selected &&
              !cancelled &&
              !userModifiedPreferences.current
            ) {
              await performSecuritySelection(selected, true);
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
        if (modeRef.current === WorkbenchMode.REPLAY) {
          liveProjection.current = applyLiveChartEvent(
            liveProjection.current,
            event,
          );
        } else {
          setProjection((current) => applyLiveChartEvent(current, event));
        }
      };
      if (envelope.event_type === "live_session_status") {
        const state = (envelope.payload as { state?: unknown })?.state;
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
        if (state === "loading" || state === "created") setLoading(true);
        if (state === "ready") {
          setLoading(false);
          setBackgroundError(null);
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
          setLoading(false);
          setActiveFailure({
            error,
            retry: active.retry,
            security: active.security,
          });
        } else {
          setBackgroundError(error);
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
        // Trade-list updates are consumed by the TradeDrawer; they are not
        // chart events and must not be routed to the workbench projection.
        // Resolve any pending trade operation the controller is tracking via
        // the event's operation_id (the controller is always mounted, even in
        // Replay, so an op started in Live is resolved here even if the Drawer
        // unmounted).
        const opId =
          typeof envelope.operation_id === "string"
            ? envelope.operation_id
            : null;
        if (opId) tradeOpController.current?.resolve(opId);
        applyLiveEvent();
        return;
      }
      const baseline = chartProjectionFromEvent(event);
      if (baseline) {
        rebaselineRequest.current = null;
        if (modeRef.current === WorkbenchMode.REPLAY) {
          liveProjection.current = applyWorkbenchSnapshot(
            liveProjection.current,
            baseline.snapshot,
            projectionIdentity(baseline),
          );
        } else {
          setProjection((current) =>
            applyWorkbenchSnapshot(
              current,
              baseline.snapshot,
              projectionIdentity(baseline),
            ),
          );
        }
        activeOperations.current.clear();
        setLoading(false);
        setBackgroundError(null);
        return;
      }
      applyLiveEvent();
    });
  }, []);

  useEffect(() => {
    if (!window.stockpilot) return;
    const stopSnapshot = window.stockpilot.onReplaySnapshot((candidate) => {
      if (modeRef.current !== WorkbenchMode.REPLAY) return;
      if (!isCompleteWorkbenchSnapshot(candidate)) return;
      const facts = replayFactsFromSnapshot(candidate);
      if (!facts) return;
      if (
        !replaySessionMatches(
          activeReplaySession.current,
          facts.sessionId,
        )
      ) {
        return;
      }
      setReplaySnapshot(candidate);
      if (modeRef.current === WorkbenchMode.REPLAY) {
        setProjection(
          createChartProjection(candidate, {
            service_generation: serviceGeneration.current,
            session_id: facts.sessionId,
            revision: candidate.session?.revision,
          }),
        );
      }
    });
    const stopEvent = window.stockpilot.onReplayEvent((event) => {
      if (modeRef.current !== WorkbenchMode.REPLAY) return;
      const envelope = replayEventEnvelope(event);
      if (!envelope) return;
      if (
        envelope.service_generation !== serviceGeneration.current ||
        !replaySessionMatches(
          activeReplaySession.current,
          envelope.session_id,
        )
      ) {
        return;
      }
      if (envelope.event_type === "operation_failed") {
        const error = applicationErrorFrom(envelope.payload);
        if (error) setBackgroundError(error);
        if (
          replayOperationMatches(
            activeReplayLoadOperation.current,
            envelope.operation_id,
          )
        ) {
          activeReplayLoadOperation.current = null;
          activeReplaySession.current = null;
          activeReplayCursorOperation.current = null;
          setReplayLoading(false);
          setReplayBusy(false);
        }
        if (
          replayOperationMatches(
            activeReplayCursorOperation.current,
            envelope.operation_id,
          )
        ) {
          activeReplayCursorOperation.current = null;
          setReplayBusy(false);
        }
        return;
      }
      if (envelope.event_type === "workbench_snapshot") {
        if (
          replayOperationMatches(
            activeReplayLoadOperation.current,
            envelope.operation_id,
          )
        ) {
          activeReplayLoadOperation.current = null;
          setReplayLoading(false);
        }
        if (
          replayOperationMatches(
            activeReplayCursorOperation.current,
            envelope.operation_id,
          )
        ) {
          activeReplayCursorOperation.current = null;
          setReplayBusy(false);
        }
        return;
      }
      if (envelope.event_type !== "session_status") return;
      const payload = envelope.payload as {
        state?: unknown;
        playback_speed?: unknown;
      };
      setReplaySnapshot((current) => {
        if (!current?.session || !current.replay) return current;
        const nextState =
          typeof payload.state === "string"
            ? payload.state
            : current.session.state;
        const nextSpeed = REPLAY_SPEEDS.includes(
          payload.playback_speed as 1 | 2 | 5 | 10,
        )
          ? (payload.playback_speed as 1 | 2 | 5 | 10)
          : current.replay.playback_speed;
        return {
          ...current,
          session: {
            ...current.session,
            state: nextState,
            revision:
              envelope.revision ?? current.session.revision,
          },
          replay: {
            ...current.replay,
            playing: nextState === "playing",
            playback_speed: nextSpeed,
          },
        };
      });
    });
    return () => {
      stopSnapshot();
      stopEvent();
    };
  }, []);

  useEffect(() => {
    if (
      !window.stockpilot ||
      !projection.rebaselineRequired ||
      projection.serviceGeneration === null ||
      projection.sessionId === null ||
      projection.revision === null
    ) {
      return;
    }
    const requestKey = [
      projection.serviceGeneration,
      projection.sessionId,
      projection.revision,
    ].join(":");
    if (rebaselineRequest.current === requestKey) return;
    rebaselineRequest.current = requestKey;
    const requestedProjection = projection;
    void window.stockpilot
      .getLiveSnapshot(
        appRequest("get_live_snapshot", projection.sessionId, {}),
      )
      .then((response) => {
        const candidate = workbenchSnapshotFromResponse(response);
        if (!candidate) return;
        setProjection((current) => {
          if (
            !current.rebaselineRequired ||
            current.serviceGeneration !==
              requestedProjection.serviceGeneration ||
            current.sessionId !== requestedProjection.sessionId
          ) {
            return current;
          }
          return applyWorkbenchSnapshot(current, candidate, {
            service_generation:
              requestedProjection.serviceGeneration ?? undefined,
            session_id: requestedProjection.sessionId,
            revision: candidate.session?.revision,
          });
        });
      })
      .catch(() => {
        // The gateway also owns the bounded rebaseline path.
      });
  }, [projection]);

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

  async function resolveSecurity(input: string) {
    if (!window.stockpilot) return null;
    try {
      const response = await window.stockpilot.selectSymbol({
        schema_version: "t0_replay_v1",
        request_id: requestId("select-symbol"),
        symbol: input,
      });
      const error = applicationErrorFrom(response);
      if (error) {
        setSearchMessage(error.message);
        return null;
      }
      return standardSecurityFromResponse(response);
    } catch (error) {
      setSearchMessage(clientError(error, "symbol_selection").message);
      return null;
    }
  }

  async function performSecuritySelection(
    security: SecurityIdentity,
    restoring = false,
  ) {
    const selectionSequence = ++securitySelectionSequence.current;
    if (!restoring) userModifiedPreferences.current = true;
    if (!window.stockpilot) {
      setWorkbench((current) =>
        selectWorkbenchSecurity(current, security),
      );
      return;
    }
    setActiveFailure(null);
    setLoading(true);
    try {
      const response = await window.stockpilot.selectSecurity(
        appRequest("select_security", null, { symbol: security.symbol }),
      );
      if (selectionSequence !== securitySelectionSequence.current) return;
      const error = applicationErrorFrom(response);
      if (error) {
        setLoading(false);
        if (!restoring) {
          setActiveFailure({ error, retry: "security", security });
        } else {
          setBackgroundError(error);
        }
        return;
      }
      const operationId = responseOperationId(response);
      const sessionId = responseSessionId(response);
      activeOperations.current.clear();
      rebaselineRequest.current = null;
      setProjection(
        beginChartSession(
          emptyChartSnapshot,
          status.service_generation,
          sessionId,
        ),
      );
      if (operationId) {
        activeOperations.current.set(operationId, {
          retry: "security",
          security,
          serviceGeneration: status.service_generation,
          sessionId,
        });
      }
      setWorkbench((current) =>
        selectWorkbenchSecurity(current, security),
      );
      setQuery(security.code);
      setSuggestions([]);
      setSearchMessage("");
    } catch (error) {
      if (selectionSequence !== securitySelectionSequence.current) return;
      setLoading(false);
      const failure = clientError(error, "symbol_selection");
      if (restoring) setBackgroundError(failure);
      else
        setActiveFailure({
          error: failure,
          retry: "security",
          security,
        });
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
      try {
        await preferenceSaveQueue.current.enqueue(
          workbenchPreferences(workbench),
        );
      } catch (error) {
        setBackgroundError(clientError(error, "preferences"));
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
      if (operationId) {
        activeOperations.current.set(operationId, {
          retry: "live",
          serviceGeneration: status.service_generation,
          sessionId: projection.sessionId,
        });
      }
    } catch (error) {
      setLoading(false);
      setBackgroundError(clientError(error, "live"));
    }
  }

  async function beginReplay() {
    if (!window.stockpilot || !workbench.security || !replayDate) return;
    setReplayLoading(true);
    setReplayBusy(false);
    setBackgroundError(null);
    try {
      const response = await window.stockpilot.beginReplay({
        schema_version: "t0_replay_v1",
        request_id: requestId("begin-replay"),
        symbol: workbench.security.symbol,
        trade_date: replayDate,
      });
      const error = applicationErrorFrom(response);
      if (error) throw error;
      const sessionId = responseSessionId(response);
      if (!sessionId) throw new TypeError("回放响应缺少 session_id");
      activeReplaySession.current = sessionId;
      activeReplayLoadOperation.current = responseOperationId(response);
      if (!activeReplayLoadOperation.current) setReplayLoading(false);
    } catch (error) {
      activeReplaySession.current = null;
      activeReplayLoadOperation.current = null;
      setReplayLoading(false);
      setBackgroundError(clientError(error, "replay"));
    }
  }

  async function setReplayPlayback(playing: boolean) {
    if (!window.stockpilot || !replayFacts) return;
    setReplayPlaybackPending(true);
    try {
      const response = await window.stockpilot.setReplayPlayback({
        schema_version: "t0_replay_v1",
        request_id: requestId(playing ? "play-replay" : "pause-replay"),
        session_id: replayFacts.sessionId,
        playing,
      });
      const error = applicationErrorFrom(response);
      if (error) throw error;
    } catch (error) {
      setBackgroundError(clientError(error, "replay"));
    } finally {
      setReplayPlaybackPending(false);
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
        schema_version: "t0_replay_v1",
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

  async function stepReplay() {
    if (!window.stockpilot || !replayFacts) return;
    setReplayBusy(true);
    try {
      const response = await window.stockpilot.stepReplay({
        schema_version: "t0_replay_v1",
        request_id: requestId("step-replay"),
        session_id: replayFacts.sessionId,
      });
      const error = applicationErrorFrom(response);
      if (error) throw error;
      activeReplayCursorOperation.current = responseOperationId(response);
      if (!activeReplayCursorOperation.current) setReplayBusy(false);
    } catch (error) {
      activeReplayCursorOperation.current = null;
      setReplayBusy(false);
      setBackgroundError(clientError(error, "replay"));
    }
  }

  async function seekReplay(targetTime: string) {
    if (!window.stockpilot || !replayFacts) return;
    setReplayBusy(true);
    try {
      const response = await window.stockpilot.seekReplay({
        schema_version: "t0_replay_v1",
        request_id: requestId("seek-replay"),
        session_id: replayFacts.sessionId,
        target_time: targetTime,
      });
      const error = applicationErrorFrom(response);
      if (error) throw error;
      activeReplayCursorOperation.current = responseOperationId(response);
      if (!activeReplayCursorOperation.current) setReplayBusy(false);
    } catch (error) {
      activeReplayCursorOperation.current = null;
      setReplayBusy(false);
      setBackgroundError(clientError(error, "replay"));
    }
  }

  function selectMode(mode: "live" | "replay") {
    if (mode === workbench.mode) return;
    modeRef.current = mode;
    if (mode === WorkbenchMode.REPLAY) {
      liveProjection.current = projection;
      updateWorkbenchFromUser((current) =>
        selectWorkbenchMode(current, WorkbenchMode.REPLAY),
      );
      return;
    }
    const sessionId = activeReplaySession.current;
    activeReplaySession.current = null;
    activeReplayLoadOperation.current = null;
    activeReplayCursorOperation.current = null;
    updateWorkbenchFromUser((current) =>
      selectWorkbenchMode(current, WorkbenchMode.LIVE),
    );
    setProjection(liveProjection.current);
    setReplaySnapshot(null);
    setReplayDate("");
    setReplayLoading(false);
    setReplayBusy(false);
    setReplayPlaybackPending(false);
    if (window.stockpilot && sessionId) {
      void window.stockpilot
        .endReplay({
          schema_version: "t0_replay_v1",
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

  const fiveMinuteModel = useMemo(
    () =>
      createChartGroupModel(
        snapshot,
        ChartGroupKind.FIVE_MINUTE,
        workbench.layers,
      ),
    [snapshot, workbench.layers],
  );
  const intradayModel = useMemo(
    () => createChartGroupModel(snapshot, ChartGroupKind.ONE_MINUTE),
    [snapshot],
  );
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
  const layoutMode = workbenchLayoutMode(workbench);
  const showFixture = !window.stockpilot;
  const showEmpty = !showFixture && !workbench.security && !loading;
  const dailyBars = latestDailyBars(snapshot);

  const replayMode = workbench.mode === WorkbenchMode.REPLAY;
  const fiveMinuteFallback =
    replayMode && replayFacts?.granularity === "five_minute";

  return (
    <main
      className={[
        "shell",
        backgroundError ? "has-feedback" : "",
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

      {backgroundError && (
        <section className="feedback-banner" role="status">
          <span>{backgroundError.message}</span>
          {backgroundError.retryable && (
            <button type="button" onClick={() => void retryLiveOrService()}>
              重试
            </button>
          )}
          <button
            type="button"
            aria-label="关闭提示"
            onClick={() => setBackgroundError(null)}
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
          {tradeFailures.map((failure) => {
            const dismissKey = failure.operationId ?? "";
            return (
              <div className="trade-feedback-item" key={dismissKey}>
                <span>{failure.message}</span>
                {failure.retry && (
                  <button
                    type="button"
                    onClick={() => {
                      const retry = failure.retry;
                      // Dismiss before retrying so the banner doesn't persist
                      // while the retry is in flight; a fresh failure is
                      // tracked by the controller if it fails again.
                      if (dismissKey) {
                        tradeOpController.current?.dismissFailure(dismissKey);
                      }
                      if (retry) void retry();
                    }}
                  >
                    重试
                  </button>
                )}
                <button
                  type="button"
                  aria-label="关闭提示"
                  onClick={() => {
                    if (dismissKey) {
                      tradeOpController.current?.dismissFailure(dismissKey);
                    }
                  }}
                >
                  ×
                </button>
              </div>
            );
          })}
        </section>
      )}

      <section
        className="workspace"
        data-chart-split={workbench.layout.chartSplit}
        data-show-intraday={workbench.layout.showIntraday}
        aria-label="T+0 三栏三行工作台"
      >
        <article className="chart-group five-minute-group" aria-label="5 分钟图表组">
          <ChartGroup
            key={`five-${workbench.security?.symbol ?? "fixture"}`}
            model={fiveMinuteModel}
            initialViewport={workbench.chartViews.fiveMinute}
            onViewportChange={(snapshot) =>
              rememberChartView("fiveMinute", snapshot)
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
          aria-label="分时图表组"
          hidden={!workbench.layout.showIntraday}
        >
          {fiveMinuteFallback ? (
            <div className="intraday-unavailable" role="status">
              无 1 分钟数据
            </div>
          ) : (
            <ChartGroup
              key={`intra-${workbench.security?.symbol ?? "fixture"}`}
              model={intradayModel}
              initialViewport={workbench.chartViews.intraday}
              onViewportChange={(snapshot) =>
                rememberChartView("intraday", snapshot)
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

        {showEmpty && (
          <div className="workspace-state empty-state">
            <strong>选择一只股票开始看盘</strong>
            <span>在顶部输入证券代码、名称或拼音首字母，并从结果中选择。</span>
          </div>
        )}
        {loading && (
          <div className="workspace-state loading-state" role="status">
            <span className="loading-spinner" aria-hidden="true" />
            <strong>正在加载历史行情并计算图表…</strong>
          </div>
        )}
      </section>

      {replayMode && (
        <ReplayControls
          tradeDate={replayDate}
          securitySelected={Boolean(workbench.security)}
          loading={replayLoading}
          busy={replayBusy}
          playbackPending={replayPlaybackPending}
          facts={replayFacts}
          onTradeDate={setReplayDate}
          onBegin={() => void beginReplay()}
          onPlayback={(playing) => void setReplayPlayback(playing)}
          onStep={() => void stepReplay()}
          onSpeed={(speed) => void setReplaySpeed(speed)}
          onSeek={(targetTime) => void seekReplay(targetTime)}
        />
      )}

      {!replayMode && (
        <TradeDrawer
          security={workbench.security}
          tradeClient={tradeClient}
          feePlanClient={feePlanClient}
          feeAdvisor={feeAdvisor}
          serviceReady={status.state === "connected" || status.state === "ready"}
          subscribeAppEvent={subscribeAppEvent}
          serviceGeneration={status.service_generation}
          tradeOpController={tradeOpController.current as TradeOperationController}
        />
      )}

      {activeFailure && (
        <div className="dialog-backdrop" role="presentation">
          <section
            className="error-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="operation-error-title"
          >
            <h2 id="operation-error-title">操作未完成</h2>
            <p>{activeFailure.error.message}</p>
            <div className="dialog-actions">
              <button type="button" onClick={() => setActiveFailure(null)}>
                关闭
              </button>
              {activeFailure.error.retryable && (
                <button
                  type="button"
                  className="primary-button"
                  onClick={() => void retryLiveOrService()}
                >
                  重试
                </button>
              )}
            </div>
          </section>
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
  onSeek: (targetTime: string) => void;
}) {
  const controls = deriveReplayControls(facts, { busy });
  const [draftProgress, setDraftProgress] = useState<number | null>(null);
  const dragging = useRef(false);
  const progress = draftProgress ?? facts?.currentValue ?? 0;
  const shownTime = facts
    ? marketClockLabel(marketTimeFromValue(progress))
    : "--:--";

  useEffect(() => {
    setDraftProgress(null);
  }, [facts?.currentValue, facts?.sessionId]);

  if (!facts) {
    return (
      <section className="replay-controls replay-setup" aria-label="回放控制面板">
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
    if (draftProgress === null || !controls.canSeek) return;
    onSeek(marketTimeFromValue(draftProgress));
  }

  return (
    <section
      className="replay-controls replay-active"
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
            if (controls.playing) onPlayback(false);
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
  const showResults =
    searching || Boolean(searchMessage) || suggestions.length > 0;
  return (
    <header className="toolbar">
      <div className="security-picker">
        <label className="sr-only" htmlFor="security-search">
          股票搜索
        </label>
        <input
          id="security-search"
          value={query}
          placeholder="代码 / 名称 / 拼音"
          autoComplete="off"
          aria-autocomplete="list"
          aria-expanded={showResults}
          aria-controls="security-results"
          onChange={(event) => onQuery(event.target.value)}
        />
        {showResults && (
          <div id="security-results" className="search-results" role="listbox">
            {searching && <div className="search-hint">正在搜索…</div>}
            {!searching &&
              suggestions.map((security) => (
                <button
                  type="button"
                  role="option"
                  key={security.symbol}
                  onClick={() => onSelect(security)}
                >
                  <span>{security.code}</span>
                  <strong>{security.name}</strong>
                  <small>{security.security_type === "etf" ? "ETF" : "A 股"}</small>
                </button>
              ))}
            {!searching && searchMessage && (
              <div className="search-hint">{searchMessage}</div>
            )}
          </div>
        )}
        <span className="security-name">{workbench.security?.name ?? ""}</span>
      </div>
      <div className="mode-switcher" aria-label="工作台模式">
        <button
          type="button"
          aria-pressed={workbench.mode === WorkbenchMode.LIVE}
          onClick={() => onMode(WorkbenchMode.LIVE)}
        >
          实盘
        </button>
        <button
          type="button"
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
    <div className="layer-switcher" aria-label="图层开关">
      {layers.map(([layer, label]) => (
        <button
          type="button"
          key={layer}
          data-layer={layer}
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
    <div className="layout-switcher" aria-label="工作台布局">
      <LayoutButton
        active={mode === WorkbenchLayoutMode.MAIN_PRIORITY}
        label="64 / 36"
        onClick={() => onSelect(WorkbenchLayoutMode.MAIN_PRIORITY)}
      />
      <LayoutButton
        active={mode === WorkbenchLayoutMode.EQUAL}
        label="50 / 50"
        onClick={() => onSelect(WorkbenchLayoutMode.EQUAL)}
      />
      <LayoutButton
        active={mode === WorkbenchLayoutMode.HIDE_INTRADAY}
        label="隐藏分时"
        onClick={() => onSelect(WorkbenchLayoutMode.HIDE_INTRADAY)}
      />
    </div>
  );
}

function LayoutButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick}>
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
    <aside className="market-sidebar" aria-label="行情栏">
      <section className="daily-chart">
        <h2>日 K</h2>
        <DailyMiniChart bars={bars} />
      </section>
      <section className="quote-panel">
        <h2>行情</h2>
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
    schema_version: "t0_app_v1",
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

function preferencesFromResponse(response: unknown) {
  if (!response || typeof response !== "object") return null;
  const data = (response as { data?: unknown }).data ?? response;
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
  if (known) return known;
  return {
    error_code: "client_operation_failed",
    message:
      capability === "preferences"
        ? "偏好暂时无法保存，当前设置仍会继续生效"
        : "操作暂时未完成，请稍后重试",
    retryable: true,
    affected_capability: capability,
  };
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

function replayEventEnvelope(event: unknown) {
  if (!event || typeof event !== "object") return null;
  const envelope = event as {
    schema_version?: unknown;
    event_type?: unknown;
    operation_id?: unknown;
    service_generation?: unknown;
    session_id?: unknown;
    revision?: unknown;
    payload?: unknown;
  };
  if (
    envelope.schema_version !== "t0_replay_v1" ||
    typeof envelope.event_type !== "string" ||
    typeof envelope.session_id !== "string"
  ) {
    return null;
  }
  return {
    event_type: envelope.event_type,
    operation_id:
      typeof envelope.operation_id === "string"
        ? envelope.operation_id
        : null,
    service_generation:
      typeof envelope.service_generation === "number"
        ? envelope.service_generation
        : null,
    session_id: envelope.session_id,
    revision:
      typeof envelope.revision === "number" ? envelope.revision : null,
    payload: envelope.payload,
  };
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
  return envelope.event_type === "workbench_snapshot" &&
    Number.isInteger(envelope.service_generation) &&
    typeof envelope.session_id === "string" &&
    Number.isInteger(envelope.revision) &&
    isCompleteWorkbenchSnapshot(envelope.payload)
    ? createChartProjection(envelope.payload, {
        service_generation: envelope.service_generation as number,
        session_id: envelope.session_id,
        revision: envelope.revision as number,
      })
    : null;
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
  return isCompleteWorkbenchSnapshot(candidate) ? candidate : null;
}

function projectionIdentity(projection: ChartProjection) {
  return {
    service_generation: projection.serviceGeneration ?? undefined,
    session_id: projection.sessionId,
    revision: projection.revision ?? undefined,
  };
}
