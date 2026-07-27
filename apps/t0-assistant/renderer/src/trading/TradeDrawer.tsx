import { useEffect, useState } from "react";
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
import type { TradeDraft } from "./trade-form.mjs";
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
  return fallback;
}

export function TradeDrawer({
  security,
  tradeClient,
  feePlanClient,
  serviceReady,
}: {
  security: SecurityIdentity | null;
  tradeClient: TradeClient | null;
  feePlanClient: FeePlanClient;
  serviceReady: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [formOpen, setFormOpen] = useState<
    | { mode: "create" }
    | { mode: "edit"; trade: TradeRecord }
    | null
  >(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [feePlans, setFeePlans] = useState<FeePlan[]>(() =>
    feePlanClient.listPlans(),
  );
  const [pendingDelete, setPendingDelete] = useState<TradeRecord | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const canList = Boolean(security && tradeClient && serviceReady);

  useEffect(() => {
    if (!canList || !tradeClient || !security) {
      setTrades([]);
      setListError(null);
      return;
    }
    let cancelled = false;
    setListError(null);
    tradeClient
      .listTrades({ symbol: security.symbol, tradeDate: localToday() })
      .then(({ trades: next }) => {
        if (!cancelled) setTrades(next);
      })
      .catch((error) => {
        if (!cancelled) {
          setTrades([]);
          setListError(errorMessage(error, "成交记录读取失败"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [canList, tradeClient, security, reloadKey]);

  function reload() {
    setReloadKey((k) => k + 1);
  }

  function refreshFeePlans() {
    setFeePlans(feePlanClient.listPlans());
  }

  async function handleSubmit(draft: TradeDraft) {
    if (!tradeClient) throw new Error("成交服务不可用");
    if (formOpen?.mode === "edit") {
      await tradeClient.updateTrade(formOpen.trade.trade_id, draft);
    } else {
      await tradeClient.createTrade(draft);
    }
    setFormOpen(null);
    reload();
  }

  async function confirmDelete() {
    if (!pendingDelete || !tradeClient) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await tradeClient.deleteTrade(pendingDelete.trade_id);
    } catch (error) {
      setDeleteBusy(false);
      setDeleteError(errorMessage(error, "成交记录删除失败"));
      return;
    }
    setDeleteBusy(false);
    setPendingDelete(null);
    reload();
  }

  const todayCount = trades.length;
  const buyCount = trades.filter((t) => t.side === "buy").length;
  const sellCount = todayCount - buyCount;

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
            onClick={() => {
              setSettingsOpen(true);
            }}
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
          onSubmit={handleSubmit}
          onClose={() => setFormOpen(null)}
        />
      )}

      <FeePlanSettingsDialog
        open={settingsOpen}
        client={feePlanClient}
        onClose={() => setSettingsOpen(false)}
        onChanged={refreshFeePlans}
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
            <p>
              确认删除该成交记录？删除后不可恢复。
            </p>
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
