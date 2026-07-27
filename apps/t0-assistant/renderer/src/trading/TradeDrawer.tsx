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
  matchTradeOperationFailed,
} from "./trade-state.mjs";
import type { SecurityIdentity } from "../workbench-layout.mjs";
import { TradeFormDialog } from "./TradeFormDialog";
import { FeePlanSettingsDialog } from "./FeePlanSettingsDialog";

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
}: {
  security: SecurityIdentity | null;
  tradeClient: TradeClient | null;
  feePlanClient: FeePlanClient | null;
  feeAdvisor: FeeAdvisor;
  serviceReady: boolean;
  subscribeAppEvent: AppEventSubscriber;
  serviceGeneration: number;
}) {
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [eventError, setEventError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [formOpen, setFormOpen] = useState<
    | { mode: "create" }
    | { mode: "edit"; trade: TradeRecord }
    | null
  >(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [feePlans, setFeePlans] = useState<FeePlan[]>(() =>
    feePlanClient ? feePlanClient.listPlans() : [],
  );
  const [pendingDelete, setPendingDelete] = useState<TradeRecord | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  // Mirror of {trades, tradeRevision} for use inside the event handler, which
  // must read the latest state without re-subscribing on every change.
  const tradeListRef = useRef<{ trades: TradeRecord[]; tradeRevision: number }>(
    { trades: [], tradeRevision: -1 },
  );
  const loadedSymbolRef = useRef<string | null>(null);
  const pendingOpsRef = useRef<Set<string>>(new Set());

  function commitTradeList(next: { trades: TradeRecord[]; tradeRevision: number }) {
    tradeListRef.current = next;
    setTrades(next.trades);
  }

  // Initial hydration via list_trades. P2: a read failure keeps the last
  // successful trades; the list is cleared only when the symbol changes.
  useEffect(() => {
    if (!security) {
      loadedSymbolRef.current = null;
      commitTradeList({ trades: [], tradeRevision: -1 });
      setListError(null);
      return;
    }
    if (!tradeClient || !serviceReady) {
      // Service unavailable is a failure state: keep the last successful list.
      return;
    }
    const symbol = security.symbol;
    const symbolChanged = loadedSymbolRef.current !== symbol;
    loadedSymbolRef.current = symbol;
    if (symbolChanged) {
      commitTradeList({ trades: [], tradeRevision: -1 });
      setListError(null);
    }
    let cancelled = false;
    const tradeDate = localToday();
    tradeClient
      .listTrades({ symbol, tradeDate })
      .then(({ trades: next, tradeRevision: revision }) => {
        if (cancelled) return;
        // Don't clobber a newer event-driven state with a stale query result.
        if (revision >= tradeListRef.current.tradeRevision) {
          commitTradeList({ trades: next, tradeRevision: revision });
        }
        setListError(null);
      })
      .catch((error) => {
        if (cancelled) return;
        setListError(errorMessage(error, "成交记录读取失败"));
      });
    return () => {
      cancelled = true;
    };
  }, [security, tradeClient, serviceReady, reloadKey]);

  // Authoritative updates via the frozen trades_changed event (P1#3). Also
  // surfaces operation_failed for tracked trade operations.
  useEffect(() => {
    if (!subscribeAppEvent) return;
    return subscribeAppEvent((event) => {
      if (
        typeof serviceGeneration === "number" &&
        serviceGeneration > 0 &&
        event &&
        typeof event === "object" &&
        typeof (event as { service_generation?: unknown }).service_generation ===
          "number" &&
        (event as { service_generation: number }).service_generation !==
          serviceGeneration
      ) {
        return;
      }
      if (!security) return;
      const scope = { symbol: security.symbol, tradeDate: localToday() };
      if (isRealTradesChangedEvent(event)) {
        const next = applyTradesChanged(tradeListRef.current, event, scope);
        if (next !== tradeListRef.current) {
          commitTradeList(next);
        }
        pendingOpsRef.current.clear();
        setEventError(null);
        return;
      }
      const failed = matchTradeOperationFailed(event, pendingOpsRef.current);
      if (failed) {
        pendingOpsRef.current.delete(failed.operationId);
        setEventError(errorMessage(failed.error, "成交操作未完成"));
      }
    });
  }, [subscribeAppEvent, security, serviceGeneration]);

  function reload() {
    setReloadKey((k) => k + 1);
  }

  function refreshFeePlans() {
    if (feePlanClient) setFeePlans(feePlanClient.listPlans());
  }

  async function handleSubmit(draft: TradeDraft) {
    if (!tradeClient) throw new Error("成交服务不可用");
    const result =
      formOpen?.mode === "edit"
        ? await tradeClient.updateTrade(formOpen.trade.trade_id, draft)
        : await tradeClient.createTrade(draft);
    setFormOpen(null);
    if (result.operationId) {
      // Async path: the authoritative list arrives via trades_changed, and an
      // operation_failed may follow. Track the id so the event is actionable.
      pendingOpsRef.current.add(result.operationId);
    } else {
      // Synchronous completion with no operation_failed path: refresh the list.
      reload();
    }
  }

  async function confirmDelete() {
    if (!pendingDelete || !tradeClient) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      const result = await tradeClient.deleteTrade(pendingDelete.trade_id);
      setPendingDelete(null);
      if (result.operationId) {
        pendingOpsRef.current.add(result.operationId);
      } else {
        reload();
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
            disabled={feePlanUnavailable}
            title={
              feePlanUnavailable ? "收费方案持久化尚未接入" : undefined
            }
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
          {eventError && (
            <div className="inline-error" role="status">
              <span>{eventError}</span>
              <button type="button" onClick={() => setEventError(null)}>
                关闭
              </button>
            </div>
          )}
          {listError ? (
            <div className="inline-error" role="status">
              <span>{listError}</span>
              <button type="button" onClick={reload}>
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
