import { Layers, Loader2, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import type { ContextCompactionProgress, ContextReport } from "../types/loom";
import "./context-meter.css";

interface ContextMeterProps {
  report: ContextReport | null;
  compacting: boolean;
  progress: ContextCompactionProgress | null;
  busy: boolean;
  onCompact(): void;
}

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1000)}k`;
  if (value >= 1_000) return `${(value / 1000).toFixed(1)}k`;
  return String(Math.max(0, Math.round(value)));
}

/** Green while there is room, amber once compaction is near, red once it bites. */
function tone(report: ContextReport): "calm" | "warm" | "hot" {
  if (report.pressure.blinded) return "hot";
  if (report.usedPercent >= 90) return "hot";
  if (report.usedPercent >= 70) return "warm";
  return "calm";
}

export function ContextMeter({ report, compacting, progress, busy, onCompact }: ContextMeterProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!report) return null;

  const budget = Math.max(1, report.inputBudgetTokens);
  const segments = report.segments.filter((segment) => segment.key !== "free");
  const level = tone(report);
  const percent = Math.round(report.usedPercent);

  const segmentLabel = (key: string): string => {
    if (key === "conversation") return zh ? "对话与指令" : "Conversation";
    if (key === "toolSchemas") return zh ? "工具定义" : "Tool definitions";
    return zh ? "空闲" : "Free";
  };

  const summary = zh
    ? `上下文预算 ${percent}%（${formatTokens(report.usedTokens)} / ${formatTokens(budget)}）`
    : `Context budget ${percent}% (${formatTokens(report.usedTokens)} / ${formatTokens(budget)})`;

  const compactionStage = (() => {
    const stage = String(progress?.stage || "queued").toLowerCase();
    if (stage === "preparing") return { index: 1, label: zh ? "整理" : "Preparing" };
    if (stage === "summarizing") return { index: 2, label: zh ? "生成摘要" : "Summarizing" };
    if (stage === "completed") return { index: 3, label: zh ? "完成" : "Done" };
    if (stage === "failed") return { index: 0, label: zh ? "失败" : "Failed" };
    return { index: 0, label: zh ? "排队" : "Queued" };
  })();

  const compactionSteps = zh
    ? ["排队", "整理历史", "生成摘要"]
    : ["Queued", "Prepare", "Summarize"];

  const compactionDescription = (() => {
    const stage = String(progress?.stage || "").toLowerCase();
    if (stage === "preparing") return zh ? "正在整理需要保留与归档的历史内容。" : "Preparing the history that will be retained and archived.";
    if (stage === "summarizing") return zh ? "正在生成交接摘要，完成后会自动刷新上下文预算。" : "Generating the handoff summary. The context budget will refresh when it finishes.";
    if (stage === "completed") return zh ? "上下文压缩已完成，新的预算已经生效。" : "Context compaction is complete and the refreshed budget is active.";
    if (stage === "failed") return zh ? "本次压缩没有完成，可以稍后重试。" : "This compaction did not complete. You can try again later.";
    return zh ? "压缩任务已创建，正在等待开始。" : "The compaction task is queued and waiting to start.";
  })();

  return (
    <div className="context-meter" ref={rootRef}>
      <button
        type="button"
        className={`context-meter-chip ${level} ${compacting ? "is-compacting" : ""}`}
        onClick={() => setOpen((value) => !value)}
        title={summary}
        aria-label={summary}
        aria-expanded={open}
      >
        {compacting ? (
          <span
            className={`context-meter-compaction-track stage-${compactionStage.index}`}
            aria-hidden="true"
          >
            {[0, 1, 2].map((step) => (
              <span
                key={step}
                className={`context-meter-compaction-step ${
                  step < compactionStage.index ? "done" : step === compactionStage.index ? "active" : ""
                }`}
              />
            ))}
          </span>
        ) : (
          <span className="context-meter-track" aria-hidden="true">
            {segments.map((segment) => (
              <span
                key={segment.key}
                className={`context-meter-fill ${segment.key}`}
                style={{ width: `${Math.min(100, (segment.tokens / budget) * 100)}%` }}
              />
            ))}
          </span>
        )}
        <span className="context-meter-value">
          {compacting ? <Loader2 size={12} strokeWidth={2} className="context-meter-spin" /> : null}
          {compacting ? compactionStage.label : `${percent}%`}
        </span>
        {report.pressure.blinded ? <span className="context-meter-alarm" aria-hidden="true" /> : null}
      </button>

      {open ? (
        <div className="context-meter-panel" role="dialog" aria-label={summary}>
          <header className="context-meter-panel-head">
            <span>{zh ? "上下文预算" : "Context budget"}</span>
            <strong>
              {formatTokens(report.usedTokens)} / {formatTokens(budget)}
            </strong>
          </header>

          <ul className="context-meter-rows">
            {report.segments.map((segment) => (
              <li key={segment.key} className={`context-meter-row ${segment.key}`}>
                <span className="context-meter-swatch" aria-hidden="true" />
                <span className="context-meter-row-label">
                  {segment.key === "toolSchemas" ? <Wrench size={11} strokeWidth={1.9} /> : null}
                  {segment.key === "conversation" ? <Layers size={11} strokeWidth={1.9} /> : null}
                  {segmentLabel(segment.key)}
                </span>
                <span className="context-meter-row-tokens">{formatTokens(segment.tokens)}</span>
                <span className="context-meter-row-percent">
                  {Math.round((segment.tokens / budget) * 100)}%
                </span>
              </li>
            ))}
          </ul>

          <dl className="context-meter-facts">
            <div>
              <dt>{zh ? "模型窗口" : "Model window"}</dt>
              <dd>
                {report.windowTokens ? formatTokens(report.windowTokens) : zh ? "未声明" : "undeclared"}
              </dd>
            </div>
            <div>
              <dt>{zh ? "自动压缩线" : "Auto-compacts at"}</dt>
              <dd>{formatTokens(report.autoCompactTokens)}</dd>
            </div>
            <div>
              <dt>{zh ? "已压缩" : "Compactions"}</dt>
              <dd>{report.compactions}</dd>
            </div>
          </dl>

          {progress ? (
            <section
              className={`context-meter-progress-card ${progress.status} stage-${compactionStage.index}`}
              role="status"
              aria-live="polite"
            >
              <div className="context-meter-progress-head">
                <span>{zh ? "压缩进度" : "Compaction progress"}</span>
                <strong>
                  {compacting ? <Loader2 size={12} strokeWidth={2} className="context-meter-spin" /> : null}
                  {compactionStage.label}
                </strong>
              </div>

              <div className="context-meter-progress-rail" aria-hidden="true">
                {compactionSteps.map((step, index) => {
                  const completed = compactionStage.index > index;
                  const active = compacting && compactionStage.index === index;
                  const failed = progress.stage === "failed" && index === compactionStage.index;
                  return (
                    <span
                      key={step}
                      className={`context-meter-progress-segment ${
                        completed ? "done" : active ? "active" : failed ? "failed" : ""
                      }`}
                    >
                      <i />
                    </span>
                  );
                })}
              </div>

              <div className="context-meter-progress-labels" aria-hidden="true">
                {compactionSteps.map((step, index) => (
                  <span
                    key={step}
                    className={index < compactionStage.index ? "done" : index === compactionStage.index ? "active" : ""}
                  >
                    {step}
                  </span>
                ))}
              </div>

              <p>{compactionDescription}</p>
              {progress.error ? <small>{progress.error}</small> : null}
            </section>
          ) : null}

          {report.pressure.blinded ? (
            <p className="context-meter-alert">
              {zh
                ? `已折叠 ${report.pressure.toolOutputsCollapsed} 条工具结果 —— 模型读不到这些命令的输出了。压缩或换更大窗口的模型可以恢复。`
                : `${report.pressure.toolOutputsCollapsed} tool results were collapsed — the agent can no longer read those command outputs. Compacting, or a larger window, restores them.`}
            </p>
          ) : null}

          {report.pressure.toolsOmitted.length ? (
            <p className="context-meter-note">
              {zh
                ? `为腾出预算，${report.pressure.toolsOmitted.length} 个工具已转为按需搜索（仍可用）。`
                : `${report.pressure.toolsOmitted.length} tools moved behind search to free budget (still callable).`}
            </p>
          ) : null}

          <button
            type="button"
            className="context-meter-compact"
            onClick={() => {
              onCompact();
            }}
            disabled={compacting || busy}
          >
            {compacting ? <Loader2 size={13} strokeWidth={2} className="context-meter-spin" /> : null}
            {compacting
              ? zh ? "正在压缩…" : "Compacting…"
              : zh ? "立即压缩上下文" : "Compact context now"}
          </button>
          {busy && !compacting ? (
            <p className="context-meter-note">
              {zh ? "当前回合结束后才能手动压缩。" : "Manual compaction waits for the current turn to finish."}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
