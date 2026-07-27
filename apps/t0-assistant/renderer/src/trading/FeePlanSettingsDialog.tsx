import { useState, type ReactNode } from "react";
import {
  createFeePlan,
  FeePlanValidationError,
  type FeePlan,
  type FeePlanClient,
  type FeePlanInput,
} from "./fee-plans.mjs";
import { TransferFeeSide, type TransferFeeSideValue } from "./fee-policy.mjs";

interface PlanFormState {
  fee_plan_id: string;
  name: string;
  a_share_commission_rate: string;
  a_share_min_commission: string;
  etf_commission_rate: string;
  etf_min_commission: string;
  stamp_duty_rate: string;
  stamp_duty_sell_only: boolean;
  transfer_fee_rate: string;
  transfer_fee_side: TransferFeeSideValue;
  transfer_fee_enabled: boolean;
}

function emptyForm(): PlanFormState {
  return {
    fee_plan_id: "",
    name: "",
    a_share_commission_rate: "0.0003",
    a_share_min_commission: "5",
    etf_commission_rate: "0.0002",
    etf_min_commission: "5",
    stamp_duty_rate: "0.0005",
    stamp_duty_sell_only: true,
    transfer_fee_rate: "0.00001",
    transfer_fee_side: TransferFeeSide.BOTH,
    transfer_fee_enabled: true,
  };
}

function formFromPlan(plan: FeePlan): PlanFormState {
  return { ...plan };
}

function formToInput(form: PlanFormState): FeePlanInput {
  return { ...form };
}

export function FeePlanSettingsDialog({
  open,
  client,
  onClose,
  onChanged,
}: {
  open: boolean;
  client: FeePlanClient;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [plans, setPlans] = useState<FeePlan[]>(() => client.listPlans());
  const [editing, setEditing] = useState<
    | { mode: "create" }
    | { mode: "edit"; plan: FeePlan }
    | null
  >(null);
  const [form, setForm] = useState<PlanFormState>(emptyForm);
  const [fieldError, setFieldError] = useState<{ field: string; message: string } | null>(
    null,
  );
  const [pendingDelete, setPendingDelete] = useState<FeePlan | null>(null);

  if (!open) return null;

  function refresh() {
    setPlans(client.listPlans());
    onChanged();
  }

  function startCreate() {
    setForm(emptyForm());
    setFieldError(null);
    setEditing({ mode: "create" });
  }

  function startEdit(plan: FeePlan) {
    setForm(formFromPlan(plan));
    setFieldError(null);
    setEditing({ mode: "edit", plan });
  }

  function submitForm() {
    if (!editing) return;
    try {
      const input = formToInput(form);
      if (editing.mode === "create") {
        client.createPlan(input);
      } else {
        client.updatePlan(input);
      }
    } catch (error) {
      if (error instanceof FeePlanValidationError) {
        setFieldError({ field: error.field, message: error.message });
        return;
      }
      setFieldError({ field: "fee_plan", message: "方案保存失败" });
      return;
    }
    setEditing(null);
    setFieldError(null);
    refresh();
  }

  function confirmDelete() {
    if (!pendingDelete) return;
    client.deletePlan(pendingDelete.fee_plan_id);
    setPendingDelete(null);
    refresh();
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="fee-plan-settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fee-plan-settings-title"
      >
        <header className="dialog-header">
          <h2 id="fee-plan-settings-title">收费方案设置</h2>
          <button
            type="button"
            aria-label="关闭"
            className="icon-button"
            onClick={onClose}
          >
            ×
          </button>
        </header>

        {editing ? (
          <div className="plan-form" aria-label="收费方案编辑表单">
            <FormField
              label="方案 ID"
              error={fieldError?.field === "fee_plan_id" ? fieldError.message : undefined}
            >
              <input
                value={form.fee_plan_id}
                disabled={editing.mode === "edit"}
                onChange={(e) =>
                  setForm({ ...form, fee_plan_id: e.target.value })
                }
              />
            </FormField>
            <FormField
              label="方案名称"
              error={fieldError?.field === "name" ? fieldError.message : undefined}
            >
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <div className="form-grid">
              <FormField
                label="A 股佣金费率"
                error={
                  fieldError?.field === "a_share_commission_rate"
                    ? fieldError.message
                    : undefined
                }
              >
                <input
                  value={form.a_share_commission_rate}
                  onChange={(e) =>
                    setForm({ ...form, a_share_commission_rate: e.target.value })
                  }
                />
              </FormField>
              <FormField
                label="A 股最低佣金"
                error={
                  fieldError?.field === "a_share_min_commission"
                    ? fieldError.message
                    : undefined
                }
              >
                <input
                  value={form.a_share_min_commission}
                  onChange={(e) =>
                    setForm({ ...form, a_share_min_commission: e.target.value })
                  }
                />
              </FormField>
              <FormField
                label="ETF 佣金费率"
                error={
                  fieldError?.field === "etf_commission_rate"
                    ? fieldError.message
                    : undefined
                }
              >
                <input
                  value={form.etf_commission_rate}
                  onChange={(e) =>
                    setForm({ ...form, etf_commission_rate: e.target.value })
                  }
                />
              </FormField>
              <FormField
                label="ETF 最低佣金"
                error={
                  fieldError?.field === "etf_min_commission"
                    ? fieldError.message
                    : undefined
                }
              >
                <input
                  value={form.etf_min_commission}
                  onChange={(e) =>
                    setForm({ ...form, etf_min_commission: e.target.value })
                  }
                />
              </FormField>
              <FormField
                label="印花税费率"
                error={
                  fieldError?.field === "stamp_duty_rate"
                    ? fieldError.message
                    : undefined
                }
              >
                <input
                  value={form.stamp_duty_rate}
                  onChange={(e) =>
                    setForm({ ...form, stamp_duty_rate: e.target.value })
                  }
                />
              </FormField>
              <FormField label="印花税仅卖出收取">
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={form.stamp_duty_sell_only}
                    onChange={(e) =>
                      setForm({ ...form, stamp_duty_sell_only: e.target.checked })
                    }
                  />
                  <span>仅卖出</span>
                </label>
              </FormField>
              <FormField
                label="过户费费率"
                error={
                  fieldError?.field === "transfer_fee_rate"
                    ? fieldError.message
                    : undefined
                }
              >
                <input
                  value={form.transfer_fee_rate}
                  onChange={(e) =>
                    setForm({ ...form, transfer_fee_rate: e.target.value })
                  }
                />
              </FormField>
              <FormField
                label="过户费收取方向"
                error={
                  fieldError?.field === "transfer_fee_side"
                    ? fieldError.message
                    : undefined
                }
              >
                <select
                  value={form.transfer_fee_side}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      transfer_fee_side: e.target.value as TransferFeeSideValue,
                    })
                  }
                >
                  <option value={TransferFeeSide.BUY}>仅买入</option>
                  <option value={TransferFeeSide.SELL}>仅卖出</option>
                  <option value={TransferFeeSide.BOTH}>买入和卖出</option>
                </select>
              </FormField>
              <FormField label="过户费启用">
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={form.transfer_fee_enabled}
                    onChange={(e) =>
                      setForm({ ...form, transfer_fee_enabled: e.target.checked })
                    }
                  />
                  <span>启用</span>
                </label>
              </FormField>
            </div>
            <div className="dialog-actions">
              <button type="button" onClick={() => setEditing(null)}>
                取消
              </button>
              <button
                type="button"
                className="primary-button"
                onClick={submitForm}
              >
                保存
              </button>
            </div>
          </div>
        ) : pendingDelete ? (
          <div className="confirm-block" role="status">
            <p>
              确认删除收费方案「{pendingDelete.name}」？删除后不可恢复。
            </p>
            <div className="dialog-actions">
              <button
                type="button"
                onClick={() => setPendingDelete(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="danger-button"
                onClick={confirmDelete}
              >
                确认删除
              </button>
            </div>
          </div>
        ) : (
          <div className="plan-list-block">
            <div className="plan-list-actions">
              <button
                type="button"
                className="primary-button"
                onClick={startCreate}
              >
                新增方案
              </button>
            </div>
            {plans.length === 0 ? (
              <p className="empty-hint">尚未维护收费方案。</p>
            ) : (
              <ul className="plan-list">
                {plans.map((plan) => (
                  <li key={plan.fee_plan_id} className="plan-item">
                    <div className="plan-item-head">
                      <strong>{plan.name}</strong>
                      <code>{plan.fee_plan_id}</code>
                    </div>
                    <dl className="plan-item-rates">
                      <div>
                        <dt>A 股佣金</dt>
                        <dd>
                          {plan.a_share_commission_rate}（最低 {plan.a_share_min_commission}）
                        </dd>
                      </div>
                      <div>
                        <dt>ETF 佣金</dt>
                        <dd>
                          {plan.etf_commission_rate}（最低 {plan.etf_min_commission}）
                        </dd>
                      </div>
                      <div>
                        <dt>印花税</dt>
                        <dd>
                          {plan.stamp_duty_rate}
                          {plan.stamp_duty_sell_only ? "（仅卖出）" : "（双边）"}
                        </dd>
                      </div>
                      <div>
                        <dt>过户费</dt>
                        <dd>
                          {plan.transfer_fee_enabled
                            ? `${plan.transfer_fee_rate}（${plan.transfer_fee_side === "both" ? "双边" : plan.transfer_fee_side === "buy" ? "仅买入" : "仅卖出"}）`
                            : "未启用"}
                        </dd>
                      </div>
                    </dl>
                    <div className="plan-item-actions">
                      <button type="button" onClick={() => startEdit(plan)}>
                        编辑
                      </button>
                      <button
                        type="button"
                        className="danger-button"
                        onClick={() => setPendingDelete(plan)}
                      >
                        删除
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

function FormField({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <label className="form-field">
      <span className="form-field-label">{label}</span>
      {children}
      {error && <span className="form-field-error">{error}</span>}
    </label>
  );
}
