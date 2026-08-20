import { useEffect, useRef, useState } from "react";
import {
  TradeClientError,
  type TradeClient,
  type TradeRecord,
} from "./trade-client.mjs";
import type { FeeAdvisor } from "./fee-advisor.mjs";
import type { FeePlan } from "./fee-plans.mjs";
import type { TradeDraft } from "./trade-form.mjs";
import { applyHistoryTradesChanged } from "./history-state.mjs";
import type { TradeOperationController } from "./trade-operation-controller.mjs";
import type { SecurityIdentity } from "../workbench-layout.mjs";
import { TradeFormDialog } from "./TradeFormDialog";

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

type HistoryListState = {
  trades: TradeRecord[];
  tradeRevision: number;
  serviceGeneration: number | null;
};

const EMPTY_HISTORY: HistoryListState = {
  trades: [],
  tradeRevision: -1,
  serviceGeneration: null,
};

/**
 * Historical trade records overlay (T0-043).
 *
 * Reached from the T+0 trade bar's "历史交易记录" entry. Shows EVERY persisted
 * real trade across all symbols and trading dates (the full repository
 * snapshot), sorted by execution time descending. No search/filter/sort/paging
 * controls. Editing reuses ``TradeFormDialog``; deletion is a single
 * confirmation with a permanent-delete warning. Both succeed through the same
 * authoritative ``trades_changed`` snapshot that also drives the day list and
 * chart markers, so the three never drift apart.
 */
export function HistoryTradesDialog({
  open,
  onClose,
  tradeClient,
  subscribeAppEvent,
  serviceGeneration,
  serviceReady,
  selectedSecurity,
  feePlans,
  feeAdvisor,
  onEnterDayChart,
  tradeOpController,
}: {
  open: boolean;
  onClose: () => void;
  tradeClient: TradeClient | null;
  subscribeAppEvent: AppEventSubscriber;
  serviceGeneration: number;
  serviceReady: boolean;
  selectedSecurity: SecurityIdentity | null;
  feePlans: FeePlan[];
  feeAdvisor: FeeAdvisor;
  onEnterDayChart: (symbol: string, tradeDate: string) => void;
  tradeOpController: TradeOperationController;
}) {
  const [history, setHistory] = useState<HistoryListState>(EMPTY_HISTORY);
  const [listError, setListError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [formOpen, setFormOpen] = useState<
    { mode: "edit"; trade: TradeRecord } | null
  >(null);
  const [pendingDelete, setPendingDelete] = useState<TradeRecord | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const historyRef = useRef<HistoryListState>(EMPTY_HISTORY);
  const prevGenRef = useRef<number>(serviceGeneration);
  const wasOpenRef = useRef<boolean>(open);

  function commitHistory(next: HistoryListState) {
    historyRef.current = next;
    setHistory(next);
  }

  // Refresh trigger via list_trades when the dialog opens (or is reloaded).
  // list_trades is a fact-via-changed-event command: its sync response carries
  // no trade data; the authoritative full snapshot arrives through
  // trades_changed. The symbol/date here are a schema-required refresh trigger
  // only - the published snapshot spans every symbol and date.
  useEffect(() => {
    if (!open || !tradeClient || !serviceReady) return;
    const symbol = selectedSecurity?.symbol ?? "sh.600000";
    let cancelled = false;
    setLoading(true);
    tradeClient
      .listTrades({ symbol, tradeDate: localToday() })
      .then(() => {
        if (!cancelled) setListError(null);
      })
      .catch((error) => {
        if (!cancelled) setListError(errorMessage(error, "历史成交读取失败"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, tradeClient, serviceReady, selectedSecurity, reloadKey]);

  // Reset the revision gate on a service_generation change (Python restart).
  useEffect(() => {
    const prev = prevGenRef.current;
    prevGenRef.current = serviceGeneration;
    if (prev === serviceGeneration || serviceGeneration <= 0) return;
    commitHistory({
      trades: historyRef.current.trades,
      tradeRevision: -1,
      serviceGeneration,
    });
    setListError(null);
    if (wasOpenRef.current) setReloadKey((k) => k + 1);
  }, [serviceGeneration]);

  useEffect(() => {
    wasOpenRef.current = open;
  }, [open]);

  // Authoritative full-snapshot updates via the frozen trades_changed event.
  useEffect(() => {
    if (!subscribeAppEvent) return;
    return subscribeAppEvent((event) => {
      const next = applyHistoryTradesChanged(historyRef.current, event);
      if (next !== historyRef.current) {
        commitHistory(next);
        if (listError) setListError(null);
      }
    });
  }, [subscribeAppEvent, listError]);

  async function handleSubmit(draft: TradeDraft) {
    if (!tradeClient || !formOpen) return;
    const tradeId = formOpen.trade.trade_id;
    // A sync rejection throws here and the form surfaces an inline retry; an
    // accepted op (operation_id null for the synchronous persist path) is
    // resolved by the authoritative trades_changed published after the write.
    const result = await tradeClient.updateTrade(tradeId, draft);
    setFormOpen(null);
    if (result.operationId) {
      tradeOpController.track(result.operationId, {
        command: "update",
        retry: async () => {
          await tradeClient.updateTrade(tradeId, draft);
        },
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
          retry: async () => {
            await tradeClient.deleteTrade(tradeId);
          },
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

  function enterDayChart(trade: TradeRecord) {
    const tradeDate =
      typeof trade.executed_at === "string" && trade.executed_at.length >= 10
        ? trade.executed_at.slice(0, 10)
        : "";
    onEnterDayChart(trade.symbol, tradeDate);
    onClose();
  }

  function identityFor(trade: TradeRecord): SecurityIdentity {
    if (selectedSecurity && selectedSecurity.symbol === trade.symbol) {
      return selectedSecurity;
    }
    // No extra request is made for unknown symbols; the name is left blank and
    // the instrument type defaults to stock so the fee advisor can still offer
    // a suggestion the user may override.
    return {
      symbol: trade.symbol,
      code: trade.symbol.slice(3),
      market: (trade.symbol.slice(0, 2) === "sz" ? "sz" : "sh") as "sh" | "sz",
      name: "",
      instrument_type: "stock",
    };
  }

  function nameFor(trade: TradeRecord): string | null {
    if (selectedSecurity && selectedSecurity.symbol === trade.symbol) {
      return selectedSecurity.name || null;
    }
    return null;
  }

  if (!open) return null;

  const trades = history.trades;

  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="history-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-trades-title"
      >
        <div className="history-dialog-header">
          <h2 id="history-trades-title">历史交易记录</h2>
          <button type="button" onClick={onClose}>
            关闭
          </button>
        </div>
        <div className="history-dialog-body">
          {listError ? (
            <div className="inline-error" role="status">
              <span>{listError}</span>
              <button type="button" onClick={() => setReloadKey((k) => k + 1)}>
                重试
              </button>
            </div>
          ) : loading && trades.length === 0 ? (
            <p className="history-loading">正在读取历史成交…</p>
          ) : !tradeClient ? (
            <p className="empty-hint">本地成交服务不可用。</p>
          ) : trades.length === 0 ? (
            <p className="empty-hint">暂无历史成交记录。</p>
          ) : (
            <ul className="history-list">
              {trades.map((trade) => {
                const name = nameFor(trade);
                return (
                  <li key={trade.trade_id} className="history-item">
                    <span
                      className={`trade-side trade-side-${trade.side}`}
                      aria-label={trade.side === "buy" ? "买入" : "卖出"}
                    >
                      {trade.side === "buy" ? "买" : "卖"}
                    </span>
                    <span className="history-datetime">{trade.executed_at}</span>
                    <span className="history-symbol">
                      {trade.symbol}
                      {name && (
                        <span className="history-symbol-name">{name}</span>
                      )}
                    </span>
                    <span className="trade-price">{trade.price}</span>
                    <span className="trade-quantity">{trade.quantity} 股</span>
                    <span className="trade-fee">
                      {trade.fee === null || trade.fee === undefined
                        ? "--"
                        : trade.fee}
                    </span>
                    <span className="history-note">{trade.note || ""}</span>
                    <span className="history-item-actions">
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
                      <button
                        type="button"
                        onClick={() => enterDayChart(trade)}
                      >
                        进入当天图形
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>

      {formOpen && (
        <TradeFormDialog
          open
          mode="edit"
          initial={formOpen.trade}
          security={identityFor(formOpen.trade)}
          feePlans={feePlans}
          feeAdvisor={feeAdvisor}
          onSubmit={handleSubmit}
          onClose={() => setFormOpen(null)}
        />
      )}

      {pendingDelete && (
        <div className="dialog-backdrop" role="presentation">
          <section
            className="confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-history-title"
          >
            <h2 id="delete-history-title">删除成交记录</h2>
            <p>确认永久删除该成交记录？删除后不可恢复。</p>
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
                {deleteBusy ? "删除中…" : "确认永久删除"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
