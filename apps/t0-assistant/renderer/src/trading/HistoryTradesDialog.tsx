import { useEffect, useRef, useState } from "react";
import {
  TradeClientError,
  type TradeClient,
  type TradeRecord,
} from "./trade-client.mjs";
import type { FeeAdvisor } from "./fee-advisor.mjs";
import type { FeePlan } from "./fee-plans.mjs";
import type { TradeDraft } from "./trade-form.mjs";
import {
  applyHistoryListResponse,
  historyInvalidatedByTradesChanged,
} from "./history-state.mjs";
import type { TradeOperationController } from "./trade-operation-controller.mjs";
import type { SecurityIdentity } from "../workbench-layout.mjs";
import { TradeFormDialog } from "./TradeFormDialog";

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
 * Historical trade records overlay (T0-043 / Issue #163).
 *
 * Hydrated from synchronous ``list_trade_history`` (full real-trade list).
 * Scoped ``trades_changed`` only marks the list dirty so the dialog re-fetches;
 * it must not merge day-scoped payloads into history. ``readOnly`` hides
 * edit/delete (Replay mode) while keeping "进入当天图形".
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
  resolveSecurity,
  readOnly = false,
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
  resolveSecurity: (symbol: string) => Promise<SecurityIdentity | null>;
  /** When true (Replay), hide edit/delete actions. */
  readOnly?: boolean;
}) {
  const [history, setHistory] = useState<HistoryListState>(EMPTY_HISTORY);
  const [listError, setListError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [formOpen, setFormOpen] = useState<
    { mode: "edit"; trade: TradeRecord } | null
  >(null);
  const [resolvedIdentity, setResolvedIdentity] =
    useState<SecurityIdentity | null>(null);
  const [identityLoading, setIdentityLoading] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<TradeRecord | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const historyRef = useRef<HistoryListState>(EMPTY_HISTORY);
  const prevGenRef = useRef<number>(serviceGeneration);
  const wasOpenRef = useRef<boolean>(open);
  const dirtyRef = useRef(false);
  const fetchInFlightRef = useRef(false);
  const fetchGenerationRef = useRef(0);

  function commitHistory(next: HistoryListState) {
    historyRef.current = next;
    setHistory(next);
  }

  function clearHistory() {
    commitHistory(EMPTY_HISTORY);
    setListError(null);
    setLoading(false);
    dirtyRef.current = false;
  }

  // On open / reload / generation bump: fetch authoritative history via
  // list_trade_history. Discard late responses with an older trade_revision.
  useEffect(() => {
    if (!open) {
      clearHistory();
      return;
    }
    if (!tradeClient || !serviceReady) return;
    if (typeof tradeClient.listTradeHistory !== "function") {
      setListError("历史成交查询尚未接入");
      return;
    }

    const fetchId = ++fetchGenerationRef.current;
    let cancelled = false;
    fetchInFlightRef.current = true;
    setLoading(true);
    dirtyRef.current = false;

    tradeClient
      .listTradeHistory({ tradeScope: "real" })
      .then((data) => {
        if (cancelled || fetchId !== fetchGenerationRef.current) return;
        const next = applyHistoryListResponse(
          historyRef.current,
          data,
          serviceGeneration,
        );
        if (next && next !== historyRef.current) {
          commitHistory(next);
        }
        setListError(null);
      })
      .catch((error) => {
        if (cancelled || fetchId !== fetchGenerationRef.current) return;
        setListError(errorMessage(error, "历史成交读取失败"));
      })
      .finally(() => {
        if (cancelled || fetchId !== fetchGenerationRef.current) return;
        fetchInFlightRef.current = false;
        setLoading(false);
        // Coalesce: a trades_changed arrived while fetching — refresh once more.
        if (dirtyRef.current && wasOpenRef.current) {
          dirtyRef.current = false;
          setReloadKey((k) => k + 1);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [open, tradeClient, serviceReady, serviceGeneration, reloadKey]);

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

  // Scoped trades_changed only invalidates; never merge into history.
  useEffect(() => {
    if (!subscribeAppEvent || !open) return;
    return subscribeAppEvent((event) => {
      if (!historyInvalidatedByTradesChanged(event)) return;
      if (fetchInFlightRef.current) {
        dirtyRef.current = true;
        return;
      }
      dirtyRef.current = false;
      setReloadKey((k) => k + 1);
    });
  }, [subscribeAppEvent, open]);

  async function handleSubmit(draft: TradeDraft) {
    if (readOnly || !tradeClient || !formOpen) return;
    const tradeId = formOpen.trade.trade_id;
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
    if (readOnly || !pendingDelete || !tradeClient) return;
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

  async function handleEditTrade(trade: TradeRecord) {
    if (readOnly) return;
    if (selectedSecurity && selectedSecurity.symbol === trade.symbol) {
      setResolvedIdentity(selectedSecurity);
      setFormOpen({ mode: "edit", trade });
      return;
    }
    setIdentityLoading(true);
    try {
      const identity = await resolveSecurity(trade.symbol);
      if (!identity) {
        setListError(`无法解析证券 ${trade.symbol} 的身份信息`);
        return;
      }
      setResolvedIdentity(identity);
      setFormOpen({ mode: "edit", trade });
    } catch (error) {
      setListError(errorMessage(error, "证券身份解析失败"));
    } finally {
      setIdentityLoading(false);
    }
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
                      {!readOnly && (
                        <>
                          <button
                            type="button"
                            disabled={identityLoading}
                            onClick={() => void handleEditTrade(trade)}
                          >
                            {identityLoading ? "解析中…" : "编辑"}
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
                        </>
                      )}
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

      {!readOnly && formOpen && resolvedIdentity && (
        <TradeFormDialog
          open
          mode="edit"
          initial={formOpen.trade}
          security={resolvedIdentity}
          feePlans={feePlans}
          feeAdvisor={feeAdvisor}
          onSubmit={handleSubmit}
          onClose={() => {
            setFormOpen(null);
            setResolvedIdentity(null);
          }}
        />
      )}

      {!readOnly && pendingDelete && (
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
