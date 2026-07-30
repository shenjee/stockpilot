import { useEffect, useMemo, useState } from "react";
import {
  buildTradeDraft,
  TradeFormValidationError,
  type TradeDraft,
} from "./trade-form.mjs";
import type { FeePlan } from "./fee-plans.mjs";
import type { FeeAdvisor } from "./fee-advisor.mjs";
import type { SecurityIdentity } from "../workbench-layout.mjs";
import type { TradeRecord } from "./trade-client.mjs";

const NO_PLAN = "";

function localNowInput() {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

function executedAtToInputValue(executedAt: string) {
  // "YYYY-MM-DD HH:MM:SS" -> "YYYY-MM-DDTHH:MM"
  const match = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/.exec(executedAt);
  return match ? `${match[1]}T${match[2]}` : localNowInput();
}

export function TradeFormDialog({
  open,
  mode,
  initial,
  security,
  feePlans,
  feeAdvisor,
  onSubmit,
  onClose,
  tradeScope = "real",
  initialExecutedAt,
}: {
  open: boolean;
  mode: "create" | "edit";
  initial: TradeRecord | null;
  security: SecurityIdentity;
  feePlans: FeePlan[];
  feeAdvisor: FeeAdvisor;
  onSubmit: (draft: TradeDraft) => Promise<void>;
  onClose: () => void;
  tradeScope?: "real" | "simulated";
  initialExecutedAt?: string;
}) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [executedAt, setExecutedAt] = useState(localNowInput);
  const [price, setPrice] = useState("");
  const [quantity, setQuantity] = useState("100");
  const [feePlanId, setFeePlanId] = useState<string>(NO_PLAN);
  const [fee, setFee] = useState("");
  const [feeTouched, setFeeTouched] = useState(false);
  const [note, setNote] = useState("");
  const [fieldError, setFieldError] = useState<{ field: string; message: string } | null>(
    null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setFieldError(null);
    setSubmitError(null);
    setSubmitting(false);
    if (mode === "edit" && initial) {
      setSide(initial.side);
      setExecutedAt(executedAtToInputValue(initial.executed_at));
      setPrice(String(initial.price));
      setQuantity(String(initial.quantity));
      setFeePlanId(initial.fee_plan_id ?? NO_PLAN);
      setFee(initial.fee === null ? "" : String(initial.fee));
      setFeeTouched(true);
      setNote(initial.note);
    } else {
      setSide("buy");
      setExecutedAt(
        initialExecutedAt
          ? executedAtToInputValue(initialExecutedAt)
          : localNowInput(),
      );
      setPrice("");
      setQuantity("100");
      setFeePlanId(NO_PLAN);
      setFee("");
      setFeeTouched(false);
      setNote("");
    }
  }, [open, mode, initial]);

  const selectedPlan = useMemo(
    () => feePlans.find((p) => p.fee_plan_id === feePlanId) ?? null,
    [feePlans, feePlanId],
  );

  // Suggest a default fee whenever the plan or computable inputs change, unless
  // the user has manually edited the fee. The suggestion comes from the
  // FeeAdvisor port (backend fee rule, not reimplemented in the renderer); the
  // null advisor returns no suggestion so the fee stays manual. The persisted
  // fee is authoritative and never recomputed after save.
  useEffect(() => {
    if (!selectedPlan || feeTouched) return;
    const suggested = feeAdvisor.suggestFee(selectedPlan, {
      securityType: security.security_type,
      side,
      price,
      quantity,
    });
    if (suggested === null || suggested === undefined) return;
    setFee(formatFee(suggested));
  }, [
    feeAdvisor,
    selectedPlan,
    feeTouched,
    side,
    price,
    quantity,
    security.security_type,
  ]);

  if (!open) return null;

  async function submit(retryDraft?: TradeDraft) {
    let draft: TradeDraft;
    if (retryDraft) {
      draft = retryDraft;
    } else {
      setFieldError(null);
      try {
        draft = buildTradeDraft(
          {
            symbol: security.symbol,
            side,
            executedAt,
            price,
            quantity,
            fee: fee === "" ? null : fee,
            note,
            feePlanId,
          },
          { tradeScope },
        );
      } catch (error) {
        if (error instanceof TradeFormValidationError) {
          setFieldError({ field: error.field, message: error.message });
        } else {
          setFieldError({ field: "form", message: "成交信息无效" });
        }
        return;
      }
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onSubmit(draft);
    } catch (error) {
      setSubmitting(false);
      setSubmitError(
        error instanceof Error ? error.message : "成交保存失败，请稍后重试",
      );
      return;
    }
    setSubmitting(false);
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="trade-form-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="trade-form-title"
      >
        <header className="dialog-header">
          <h2 id="trade-form-title">
            {mode === "create" ? "录入成交" : "编辑成交"}
          </h2>
          <button
            type="button"
            aria-label="关闭"
            className="icon-button"
            onClick={onClose}
            disabled={submitting}
          >
            ×
          </button>
        </header>

        <div className="trade-form-body">
          <div className="form-field">
            <span className="form-field-label">股票</span>
            <output className="form-readonly">
              {security.code} {security.name}
            </output>
          </div>

          <div className="form-field">
            <span className="form-field-label">方向</span>
            <div className="side-toggle" role="group" aria-label="买卖方向">
              <button
                type="button"
                aria-pressed={side === "buy"}
                data-side="buy"
                onClick={() => setSide("buy")}
              >
                买入
              </button>
              <button
                type="button"
                aria-pressed={side === "sell"}
                data-side="sell"
                onClick={() => setSide("sell")}
              >
                卖出
              </button>
            </div>
          </div>

          <div className="form-grid">
            <label className="form-field">
              <span className="form-field-label">成交时间</span>
              <input
                type="datetime-local"
                step={60}
                value={executedAt}
                onChange={(e) => setExecutedAt(e.target.value)}
              />
              {fieldError?.field === "executed_at" && (
                <span className="form-field-error">{fieldError.message}</span>
              )}
            </label>
            <label className="form-field">
              <span className="form-field-label">成交价格</span>
              <input
                inputMode="decimal"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
              />
              {fieldError?.field === "price" && (
                <span className="form-field-error">{fieldError.message}</span>
              )}
            </label>
            <label className="form-field">
              <span className="form-field-label">成交数量（股）</span>
              <input
                inputMode="numeric"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              />
              {fieldError?.field === "quantity" && (
                <span className="form-field-error">{fieldError.message}</span>
              )}
            </label>
            <label className="form-field">
              <span className="form-field-label">收费方案</span>
              <select
                value={feePlanId}
                onChange={(e) => {
                  setFeePlanId(e.target.value);
                  setFeeTouched(false);
                  setFee("");
                }}
              >
                <option value={NO_PLAN}>不计算</option>
                {feePlans.map((plan) => (
                  <option key={plan.fee_plan_id} value={plan.fee_plan_id}>
                    {plan.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="form-field">
              <span className="form-field-label">手续费（可选，可覆盖）</span>
              <input
                inputMode="decimal"
                value={fee}
                placeholder="可手填或留空"
                onChange={(e) => {
                  setFee(e.target.value);
                  setFeeTouched(true);
                }}
              />
              {fieldError?.field === "fee" && (
                <span className="form-field-error">{fieldError.message}</span>
              )}
            </label>
            <label className="form-field form-field-wide">
              <span className="form-field-label">备注（可选）</span>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
          </div>

          {submitError && (
            <div className="inline-error" role="status">
              <span>{submitError}</span>
              <button type="button" onClick={() => void submit()}>
                重试
              </button>
            </div>
          )}

          <div className="dialog-actions">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
            >
              取消
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={() => void submit()}
              disabled={submitting}
            >
              {submitting ? "保存中…" : "保存成交"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function formatFee(value: number) {
  // Keep up to four decimals, strip trailing zeros for a clean suggestion.
  return String(Number(value.toFixed(4)));
}
