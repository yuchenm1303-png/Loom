import type { ContextReport } from "../types/loom";

/** Treat legacy fallback-window reports as unknown too. */
export function contextMeterModel(report: ContextReport) {
  const declared = report.windowKnown || report.budgetBasis === "observed" || report.budgetBasis === "working";
  const budget = declared && report.inputBudgetTokens && report.inputBudgetTokens > 0 ? report.inputBudgetTokens : null;
  const percent = budget === null ? null : Math.round(report.usedTokens / budget * 100);
  return { budget, percent, unknown: budget === null,
    autoCompactTokens: declared ? report.autoCompactTokens : null,
    windowTokens: report.windowKnown ? report.windowTokens : null };
}
