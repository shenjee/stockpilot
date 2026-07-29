import { useEffect, useRef, useState } from "react";
import {
  createTradeClient,
  TradeClientError,
  type TradeClient,
  type TradeRecord,
} from "./trade-client.mjs";
import {
  createInMemoryFeePlanClient,
  type FeePlan,
  type FeePlanClient,
} from "./fee-plans.mjs";
import type { FeeAdvisor } from "./fee-advisor.mjs";
import type { TradeDraft } from "./trade-form.mjs";
import {
  applyTradesChanged,
  isRealTradesChangedEvent,
  type TradeListState,
} from "./trade-state.mjs";
import type { TradeOperationController } from "./trade-operation-controller.mjs";
import type { SecurityIdentity } from "../workbench-layout.mjs";
import { TradeFormDialog } from "./TradeFormDialog";
import { FeePlanSettingsDialog } from "./FeePlanSettingsDialog";
import { HistoryTradesDialog } from "./HistoryTradesDialog";

function localToday() {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function errorMessage(error: unknown, fallback: string) {
  if (error instanceof TradeClientError) return error.message;
  if (error instanceof Error) return error.message;
  if (
    error &&
    typeof error === "object" &&
    typeof (error as { message?: unknown }).message === "string"
  ) {
    return (error as { message: string }).message;
  }
  return fallback;
}

type AppEventSubscriber =
  | ((listener: (event: unknown) => void) => () => void)
  | null;

export function TradeDrawer({
  security,
  tradeClient,
  feePlanClient,
  feeAdvisor,
  serviceReady,
  subscribeAppEvent,
  serviceGeneration,
  tradeOpController,
  onEnterDayChart,
}: {
  security: SecurityIdentity | null;
  tradeClient: TradeClient | null;
  feePlanClient: FeePlanClient | null;
  feeAdvisor: FeeAdvisor;
  serviceReady: boolean;
  subscribeAppEvent: AppEventSubscriber;
  serviceGeneration: number;
  /**
   * App-owned, always-mounted controller for pending trade operations and
   * their retry context. The Drawer delegates track/fail to it so a trade op
   * started in Live that fails after the user switches to Replay is still
   * surfaced with the correct CRUD retry (the controller survives unmount).
   */
  tradeOpController: TradeOperationController;
  /**
   * Open the static workbench for a historical trade's symbol + trading date
   * (T0-043 "进入当天图形"). Does not start Replay playback.
   */
  onEnterDayChart: (symbol: string, tradeDate: string) => void;
}) {
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [formOpen, setFormOpen] = useState<
    | { mode: "create" }
    | { mode: "edit"; trade: TradeRecord }
    | null
  >(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [feePlans, setFeePlans] = useState<FeePlan[]>(() =>
    feePlanClient ? feePlanClient.listPlans() : [],
  );
  const [pendingDelete, setPendingDelete] = useState<TradeRecord | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  // Mirror of the trade-list state for use inside the event handler.
  const tradeListRef = useRef<TradeListState>({
    trades: [],
    tradeRevision: -1,
    serviceGeneration: null,
  });
  const loadedSymbolRef = useRef<string | null>(null);
  const prevGenRef = useRef<number>(serviceGeneration);

  function commitTradeList(next: TradeListState) {
    tradeListRef.current = next;
    setTrades(next.trades);
  }

  // Refresh trigger via list_trades. The trade list itself is NOT read from
  // the response (its data shape is unfrozen); the authoritative list arrives
  // through trades_changed. A trigger failure surfaces a retry error but does
  // not clear the last successful list.
  useEffect(() => {
    if (!security) {
      loadedSymbolRef.current = null;
      commitTradeList({
        trades: [],
        tradeRevision: -1,
        serviceGeneration: tradeListRef.current.serviceGeneration,
      });
      setListError(null);
      return;
    }
    if (!tradeClient || !serviceReady) {
      // Service unavailable: keep the last successful list.
      return;
    }
    const symbol = security.symbol;
    const symbolChanged = loadedSymbolRef.current !== symbol;
    loadedSymbolRef.current = symbol;
    if (symbolChanged) {
      commitTradeList({
        trades: [],
        tradeRevision: -1,
        serviceGeneration: tradeListRef.current.serviceGeneration,
      });
      setListError(null);
    }
    let cancelled = false;
    tradeClient
      .listTrades({ symbol, tradeDate: localToday() })
      .then(() => {
        if (!cancelled) setListError(null);
      })
      .catch((error) => {
        if (!cancelled) setListError(errorMessage(error, "成交记录读取失败"));
      });
    return () => {
      cancelled = true;
    };
  }, [security, tradeClient, serviceReady, reloadKey]);

  // Reset the revision gate on a service_generation change (Python restart).
  useEffect(() => {
    const prev = prevGenRef.current;
    prevGenRef.current = serviceGeneration;
    if (prev === serviceGeneration || serviceGeneration <= 0) return;
    tradeListRef.current = {
      ...tradeListRef.current,
      tradeRevision: -1,
      serviceGeneration,
    };
    setListError(null);
    setReloadKey((k) => k + 1);
  }, [serviceGeneration]);

  // Authoritative list updates via the frozen trades_changed event. Pending-op
  // resolution and operation_failed are handled by the App-level controller
  // (so they survive this Drawer unmounting in Replay); this subscription only
  // maintains the visible trade list.
  useEffect(() => {
    if (!subscribeAppEvent) return;
    return subscribeAppEvent((event) => {
      if (!security) return;
      if (!isRealTradesChangedEvent(event)) return;
      const scope = { symbol: security.symbol, tradeDate: localToday() };
      const next = applyTradesChanged(tradeListRef.current, event, scope);
      if (next !== tradeListRef.current) {
        commitTradeList(next);
      }
    });
  }, [subscribeAppEvent, security]);

  function refreshFeePlans() {
    if (feePlanClient) setFeePlans(feePlanClient.listPlans());
  }

  // Submit a create/update, tracking the async operation on the App-level
  // controller so its retry survives a mode switch. Sync rejections surface on
  // the controller's persistent banner too (so they aren't lost on unmount).
  async function reRunCreate(draft: TradeDraft) {
    if (!tradeClient) return;
    try {
      const result = await tradeClient.createTrade(draft);
      if (result.operationId) {
        tradeOpController.track(result.operationId, {
          command: "create",
          retry: () => reRunCreate(draft),
        });
      } else {
        setReloadKey((k) => k + 1);
      }
    } catch (error) {
      // Sync rejection: no operation_id (the command threw before returning
      // one). Surface it as an untracked failure, but carry command/retry so
      // the user can retry the failed create again.
      tradeOpController.failUntracked(
        null,
        errorMessage(error, "成交保存失败"),
        error,
        { command: "create", retry: () => reRunCreate(draft) },
      );
    }
  }

  async function reRunUpdate(tradeId: string, draft: TradeDraft) {
    if (!tradeClient) return;
    try {
      const result = await tradeClient.updateTrade(tradeId, draft);
      if (result.operationId) {
        tradeOpController.track(result.operationId, {
          command: "update",
          retry: () => reRunUpdate(tradeId, draft),
        });
      } else {
        setReloadKey((k) => k + 1);
      }
    } catch (error) {
      tradeOpController.failUntracked(
        null,
        errorMessage(error, "成交保存失败"),
        error,
        { command: "update", retry: () => reRunUpdate(tradeId, draft) },
      );
    }
  }

  async function reRunDelete(tradeId: string) {
    if (!tradeClient) return;
    try {
      const result = await tradeClient.deleteTrade(tradeId);
      if (result.operationId) {
        tradeOpController.track(result.operationId, {
          command: "delete",
          retry: () => reRunDelete(tradeId),
        });
      } else {
        setReloadKey((k) => k + 1);
      }
    } catch (error) {
      tradeOpController.failUntracked(
        null,
        errorMessage(error, "成交记录删除失败"),
        error,
        { command: "delete", retry: () => reRunDelete(tradeId) },
      );
    }
  }

  async function handleSubmit(draft: TradeDraft) {
    if (!tradeClient) throw new Error("成交服务不可用");
    if (!formOpen) return;
    const mode = formOpen.mode;
    const tradeId = mode === "edit" ? formOpen.trade.trade_id : null;
    // A sync rejection throws here and the form surfaces an inline retry; an
    // accepted async op is tracked on the controller for a later
    // operation_failed retry (which survives a mode switch).
    const result =
      mode === "edit"
        ? await tradeClient.updateTrade(tradeId as string, draft)
        : await tradeClient.createTrade(draft);
    setFormOpen(null);
    if (result.operationId) {
      const retry =
        mode === "edit"
          ? () => reRunUpdate(tradeId as string, draft)
          : () => reRunCreate(draft);
      tradeOpController.track(result.operationId, {
        command: mode === "edit" ? "update" : "create",
        retry,
      });
    } else {
      setReloadKey((k) => k + 1);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete || !tradeClient) return;
    const tradeId = pendingDelete.trade_id;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      const result = await tradeClient.deleteTrade(tradeId);
      setPendingDelete(null);
      if (result.operationId) {
        tradeOpController.track(result.operationId, {
          command: "delete",
          retry: () => reRunDelete(tradeId),
        });
      } else {
        setReloadKey((k) => k + 1);
      }
    } catch (error) {
      setDeleteError(errorMessage(error, "成交记录删除失败"));
    } finally {
      setDeleteBusy(false);
    }
  }

  const todayCount = trades.length;
  const buyCount = trades.filter((t) => t.side === "buy").length;
  const sellCount = todayCount - buyCount;
  const feePlanUnavailable = feePlanClient === null;

  return (
    <footer className="trade-drawer" aria-label="T+0 成交折叠栏">
      <div className="trade-drawer-bar">
        <button
          type="button"
          className="trade-drawer-toggle"
          aria-expanded={expanded}
          aria-label={expanded ? "收起成交栏" : "展开成交栏"}
          onClick={() => setExpanded((v) => !v)}
        >
          <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
          <span>T+0 成交</span>
        </button>
        <span className="trade-drawer-summary">
          {security
            ? `今日 ${todayCount} 笔（买 ${buyCount} / 卖 ${sellCount}）`
            : "请先选择股票"}
        </span>
        <div className="trade-drawer-actions">
          <button
            type="button"
            className="primary-button"
            disabled={!security || !tradeClient}
            onClick={() => setFormOpen({ mode: "create" })}
          >
            录入成交
          </button>
          <button
            type="button"
            disabled={!tradeClient}
            title={!tradeClient ? "本地成交服务不可用" : undefined}
            onClick={() => setHistoryOpen(true)}
          >
            历史交易记录
          </button>
          <button
            type="button"
            disabled={feePlanUnavailable}
            title={feePlanUnavailable ? "收费方案持久化尚未接入" : undefined}
            onClick={() => setSettingsOpen(true)}
          >
            收费方案设置
          </button>
        </div>
      </div>

      {expanded && (
        <div
          className="trade-drawer-panel"
          role="region"
          aria-label="当日成交记录"
        >
          {listError ? (
            <div className="inline-error" role="status">
              <span>{listError}</span>
              <button type="button" onClick={() => setReloadKey((k) => k + 1)}>
                重试
              </button>
            </div>
          ) : !security ? (
            <p className="empty-hint">请先在顶部选择股票后再录入或查看成交。</p>
          ) : !tradeClient ? (
            <p className="empty-hint">本地成交服务不可用，仅可维护收费方案。</p>
          ) : trades.length === 0 ? (
            <p className="empty-hint">今日暂无成交记录。</p>
          ) : (
            <ul className="trade-list">
              {trades.map((trade) => (
                <li key={trade.trade_id} className="trade-item">
                  <span
                    className={`trade-side trade-side-${trade.side}`}
                    aria-label={trade.side === "buy" ? "买入" : "卖出"}
                  >
                    {trade.side === "buy" ? "买" : "卖"}
                  </span>
                  <span className="trade-time">
                    {trade.executed_at.slice(11, 19)}
                  </span>
                  <span className="trade-price">{trade.price}</span>
                  <span className="trade-quantity">{trade.quantity} 股</span>
                  <span className="trade-fee">
                    费 {trade.fee === null ? "--" : trade.fee}
                  </span>
                  {trade.note && (
                    <span className="trade-note">{trade.note}</span>
                  )}
                  <span className="trade-item-actions">
                    <button
                      type="button"
                      onClick={() => setFormOpen({ mode: "edit", trade })}
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      className="danger-button"
                      onClick={() => {
                        setDeleteError(null);
                        setPendingDelete(trade);
                      }}
                    >
                      删除
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {formOpen && security && (
        <TradeFormDialog
          open
          mode={formOpen.mode}
          initial={formOpen.mode === "edit" ? formOpen.trade : null}
          security={security}
          feePlans={feePlans}
          feeAdvisor={feeAdvisor}
          onSubmit={handleSubmit}
          onClose={() => setFormOpen(null)}
        />
      )}

      {!feePlanUnavailable && (
        <FeePlanSettingsDialog
          open={settingsOpen}
          client={feePlanClient}
          onClose={() => setSettingsOpen(false)}
          onChanged={refreshFeePlans}
        />
      )}

      <HistoryTradesDialog
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        tradeClient={tradeClient}
        subscribeAppEvent={subscribeAppEvent}
        serviceGeneration={serviceGeneration}
        serviceReady={serviceReady}
        selectedSecurity={security}
        feePlans={feePlans}
        feeAdvisor={feeAdvisor}
        onEnterDayChart={onEnterDayChart}
        tradeOpController={tradeOpController}
      />

      {pendingDelete && (
        <div className="dialog-backdrop" role="presentation">
          <section
            className="confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-trade-title"
          >
            <h2 id="delete-trade-title">删除成交记录</h2>
            <p>确认删除该成交记录？删除后不可恢复。</p>
            {deleteError && (
              <div className="inline-error" role="status">
                <span>{deleteError}</span>
              </div>
            )}
            <div className="dialog-actions">
              <button
                type="button"
                onClick={() => setPendingDelete(null)}
                disabled={deleteBusy}
              >
                取消
              </button>
              <button
                type="button"
                className="danger-button"
                onClick={() => void confirmDelete()}
                disabled={deleteBusy}
              >
                {deleteBusy ? "删除中…" : "确认删除"}
              </button>
            </div>
          </section>
        </div>
      )}
    </footer>
  );
}

/**
 * Build a `TradeClient` bound to the frozen Safe Bridge. Returns `null` when
 * the bridge is unavailable (fixture mode) so the drawer can degrade to a
 * fee-plan-only surface.
 */
export function createBoundTradeClient(bridge: unknown): TradeClient | null {
  if (
    !bridge ||
    typeof bridge !== "object" ||
    typeof (bridge as { createTrade?: unknown }).createTrade !== "function"
  ) {
    return null;
  }
  return createTradeClient(bridge as Parameters<typeof createTradeClient>[0]);
}

export { createInMemoryFeePlanClient };
