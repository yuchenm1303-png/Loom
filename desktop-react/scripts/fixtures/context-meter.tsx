import React from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { I18nProvider } from "../../src/i18n";
import { ContextMeter } from "../../src/components/ContextMeter";
import type { ContextReport } from "../../src/types/loom";
import "../../src/styles.css";
import "../../src/theme.css";

const root = createRoot(document.getElementById("root")!);
const unknown: ContextReport = {
  windowKnown: false, windowTokens: 272000, effectiveWindowTokens: 258400,
  inputBudgetTokens: 254304, outputReserveTokens: 4096, autoCompactTokens: 244800,
  toolOutputTokenLimit: 9500, limitsSource: "runtime_fallback", usedTokens: 377721,
  usedPercent: 100, freeTokens: 0, accounting: "provider_usage", messageCount: 570,
  segments: [{ key: "conversation", tokens: 376000 }, { key: "toolSchemas", tokens: 1721 }, { key: "free", tokens: 0 }],
  pressure: { schemaMode: "compact", toolsOmitted: [], toolOutputsReduced: 0,
    toolOutputsCollapsed: 0, userMessagesTruncated: 0, blinded: false },
  compactions: 0, lastCompactedAt: "", measuredAt: "2026-10-06", measurementPending: false,
};
(window as unknown as { renderMeter(mode: string): void }).renderMeter = (mode) => {
  const report = mode === "unknown" ? unknown : { ...unknown, windowKnown: true,
    windowTokens: 512000, inputBudgetTokens: 482304, usedTokens: 64000, usedPercent: 13.3,
    autoCompactTokens: 128000, freeTokens: 418304 };
  const compacting = mode === "compacting";
  flushSync(() => root.render(<I18nProvider>
    <div style={{ padding: 30, height: 700 }}><ContextMeter report={report} compacting={compacting}
      progress={compacting ? { threadId: "test", operationId: "test", status: "running", stage: "summarizing", message: "", error: "", startedAt: "", updatedAt: "" } : null}
      busy={compacting} onCompact={() => {}} /></div>
  </I18nProvider>));
};
