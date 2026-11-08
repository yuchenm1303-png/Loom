import { useEffect, useState } from "react";
import "./goal-budget-control.css";
import { AnimatedFold } from "../motion/AnimatedFold";

export type GoalBudgetState = { tokens: number; tokenBudget: number; startedAt?: number };
export type GoalBudgetUpdate = { tokenBudget: number; expectedTokenBudget: number; expectedStartedAt?: number };
export function goalBudgetUpdate(goal: GoalBudgetState, value: string, mode: "total" | "add"): GoalBudgetUpdate {
  const amount = Number(value);
  const total = mode === "add" ? goal.tokenBudget + amount : amount;
  if (!value.trim() || !Number.isSafeInteger(amount) || amount <= 0 || !Number.isSafeInteger(total) || total > 1e9 || total <= goal.tokens) throw new Error("请填写整数 token：新总上限须大于已用量，且不能超过 10 亿。");
  return { tokenBudget: total, expectedTokenBudget: goal.tokenBudget, ...(goal.startedAt !== undefined ? { expectedStartedAt: goal.startedAt } : {}) };
}
export function GoalBudgetControl({ goal, disabled, canResume, onSubmit }: {
  goal: GoalBudgetState; disabled?: boolean; canResume: boolean;
  onSubmit(action: "set_budget" | "resume", update: GoalBudgetUpdate): Promise<unknown>;
}) {
  const spent = goal.tokens >= goal.tokenBudget;
  const [open, setOpen] = useState(spent);
  const [mode, setMode] = useState<"total" | "add">("total");
  const [value, setValue] = useState(String(goal.tokenBudget));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => { setValue(String(goal.tokenBudget)); setMode("total"); setError(""); }, [goal.startedAt, goal.tokenBudget]);
  let update: GoalBudgetUpdate | null = null;
  try { update = goalBudgetUpdate(goal, value, mode); } catch { /* Invalid drafts stay editable. */ }
  const submit = async (action: "set_budget" | "resume") => {
    if (busy || disabled) return;
    setError(""); setSaved(false);
    try {
      const params = goalBudgetUpdate(goal, value, mode);
      setBusy(true);
      if (await onSubmit(action, params) === false) throw new Error("预算未保存，请查看错误信息并重试。");
      setSaved(true);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  return <div className="goal-budget-control">
    <p className="goal-budget-balance">已用 {goal.tokens.toLocaleString()} · 总额 {goal.tokenBudget.toLocaleString()} · 剩余 {Math.max(0, goal.tokenBudget - goal.tokens).toLocaleString()} token</p>
    <button type="button" className="goal-budget-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>调整长目标预算{spent ? " · 已耗尽" : ""}</button>
    <AnimatedFold open={open}><div className="goal-budget-editor">
      <div className="goal-budget-modes" role="group" aria-label="预算设置方式"><button type="button" aria-pressed={mode === "total"} disabled={busy} onClick={() => { setMode("total"); setValue(String(goal.tokenBudget)); setError(""); setSaved(false); }}>设置总上限</button><button type="button" aria-pressed={mode === "add"} disabled={busy} onClick={() => { setMode("add"); setValue(""); setError(""); setSaved(false); }}>追加额度</button></div>
      <label>{mode === "total" ? "长目标总 token 上限" : "追加 token 数量"}<input type="number" inputMode="numeric" min="1" max="1000000000" step="1" value={value} disabled={disabled || busy} onChange={(event) => { setValue(event.target.value); setSaved(false); }} /></label>
      {update ? <p>修改后总额 {update.tokenBudget.toLocaleString()}，预计剩余 {Math.max(0, update.tokenBudget - goal.tokens).toLocaleString()} token。</p> : <p>新总上限必须大于已用量。</p>}
      <p>只按你填写的额度修改，不会自动翻倍。已发出的模型请求仍会完成并计入用量。</p>
      <div className="goal-budget-actions"><button type="button" disabled={disabled || busy || !update} onClick={() => void submit("set_budget")}>仅保存预算</button>{canResume ? <button type="button" disabled={disabled || busy || !update} onClick={() => void submit("resume")}>保存预算并继续</button> : null}</div>
      <AnimatedFold open={Boolean(error)}><p role="alert">{error}</p></AnimatedFold><AnimatedFold open={saved}><p role="status">预算已保存。</p></AnimatedFold>
    </div></AnimatedFold>
    {spent && canResume ? <p role="status">预算已耗尽，请先填写新的总上限，再保存并继续。</p> : null}
  </div>;
}
