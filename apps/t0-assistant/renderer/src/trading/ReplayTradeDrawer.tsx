import { useEffect, useMemo, useState } from "react";
import type { SecurityIdentity } from "../workbench-layout.mjs";
import { createNullFeeAdvisor } from "./fee-advisor.mjs";
import type { TradeClient, TradeRecord } from "./trade-client.mjs";
import type { TradeDraft } from "./trade-form.mjs";
import { TradeFormDialog } from "./TradeFormDialog";

export function ReplayTradeDrawer({
  security,
  sessionId,
  currentTime,
  tradeClient,
  subscribeAppEvent,
  onTradesChange,
}: {
  security: SecurityIdentity;
  sessionId: string;
  currentTime: string;
  tradeClient: TradeClient;
  subscribeAppEvent: ((listener: (event: unknown) => void) => () => void) | null;
  onTradesChange: (trades: TradeRecord[]) => void;
}) {
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [availability, setAvailability] = useState<
    "checking" | "available" | "unavailable"
  >("checking");
  const [form, setForm] = useState<
    { mode: "create" } | { mode: "edit"; trade: TradeRecord } | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const feeAdvisor = useMemo(() => createNullFeeAdvisor(), []);

  function replace(next: TradeRecord[]) {
    const ordered = [...next].sort((a, b) =>
      a.executed_at.localeCompare(b.executed_at) ||
      a.trade_id.localeCompare(b.trade_id),
    );
    setTrades(ordered);
    onTradesChange(ordered);
  }

  useEffect(() => {
    let active = true;
    replace([]);
    setAvailability("checking");
    setError(null);
    void tradeClient
      .listTrades({
        symbol: security.symbol,
        tradeDate: currentTime.slice(0, 10),
        tradeScope: "simulated",
      })
      .then(() => {
        if (active) setAvailability("available");
      })
      .catch((cause) => {
        if (!active) return;
        setAvailability("unavailable");
        setError(
          cause instanceof Error
            ? cause.message
            : "模拟成交服务尚未接入，当前不可录入",
        );
      });
    return () => {
      active = false;
      onTradesChange([]);
    };
  }, [sessionId, tradeClient, security.symbol]);

  useEffect(() => {
    if (!subscribeAppEvent) return;
    return subscribeAppEvent((candidate) => {
      const event = candidate as {
        session_id?: unknown;
        event_type?: unknown;
        payload?: { trades?: unknown };
      };
      if (
        event.session_id !== sessionId ||
        event.event_type !== "trades_changed" ||
        !Array.isArray(event.payload?.trades)
      ) {
        return;
      }
      setAvailability("available");
      replace(event.payload.trades as TradeRecord[]);
      setError(null);
    });
  }, [subscribeAppEvent, sessionId]);

  async function submit(draft: TradeDraft) {
    if (availability !== "available") {
      throw new Error("模拟成交服务当前不可用");
    }
    try {
      if (form?.mode === "edit") {
        await tradeClient.updateTrade(form.trade.trade_id, draft);
      } else {
        await tradeClient.createTrade(draft);
      }
      setForm(null);
    } catch (cause) {
      throw cause instanceof Error ? cause : new Error("模拟成交保存失败");
    }
  }

  async function remove(trade: TradeRecord) {
    if (!window.confirm("确认删除这笔模拟成交？")) return;
    try {
      await tradeClient.deleteTrade(trade.trade_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "模拟成交删除失败");
    }
  }

  return (
    <footer className="trade-drawer" aria-label="回放模拟成交折叠栏">
      <div className="trade-drawer-bar">
        <button
          type="button"
          className="trade-drawer-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
          <span>模拟成交</span>
        </button>
        <span className="trade-drawer-summary">
          {availability === "available"
            ? `当前回放会话 ${trades.length} 笔（退出后清空）`
            : availability === "checking"
              ? "正在检查模拟成交服务…"
              : "模拟成交服务尚未接入，当前不可录入"}
        </span>
        <button
          type="button"
          className="primary-button"
          disabled={availability !== "available"}
          title={
            availability === "unavailable"
              ? "当前正式启动路径尚未提供 Replay Session 成交服务"
              : undefined
          }
          onClick={() => setForm({ mode: "create" })}
        >
          录入模拟成交
        </button>
      </div>
      {expanded && (
        <div className="trade-drawer-content">
          {trades.length === 0 ? (
            <p>本次回放尚无模拟成交。</p>
          ) : (
            trades.map((trade) => (
              <div key={trade.trade_id} className="trade-list-row">
                <span>{trade.side === "buy" ? "买入" : "卖出"}</span>
                <span>{trade.executed_at.slice(11, 16)}</span>
                <span>{trade.price}</span>
                <span>{trade.quantity} 股</span>
                <button type="button" onClick={() => setForm({ mode: "edit", trade })}>
                  编辑
                </button>
                <button type="button" onClick={() => void remove(trade)}>
                  删除
                </button>
              </div>
            ))
          )}
        </div>
      )}
      {error && <div className="inline-error" role="status">{error}</div>}
      {form && (
        <TradeFormDialog
          open
          mode={form.mode}
          initial={form.mode === "edit" ? form.trade : null}
          security={security}
          feePlans={[]}
          feeAdvisor={feeAdvisor}
          tradeScope="simulated"
          initialExecutedAt={currentTime}
          onSubmit={submit}
          onClose={() => setForm(null)}
        />
      )}
    </footer>
  );
}
