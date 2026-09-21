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
        <span className="context-meter-track" aria-hidden="true">
          {segments.map((segment) => (
            <span
              key={segment.key}
              className={`context-meter-fill ${segment.key}`}
              style={{ width: `${Math.min(100, (segment.tokens / budget) * 100)}%` }}
            />
          ))}
        </span>
        <span className="context-meter-value">
          {compacting ? <Loader2 size={12} strokeWidth={2} className="context-meter-spin" /> : null}
          {compacting ? (zh ? "压缩中" : "Compacting") : `${percent}%`}
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
          {progress ? (
            <p className={`context-meter-note context-meter-progress ${progress.status}`} role="status">
              <strong>
                {progress.stage === "queued" ? (zh ? "已排队" : "Queued") : null}
                {progress.stage === "preparing" ? (zh ? "正在整理历史" : "Preparing history") : null}
                {progress.stage === "summarizing" ? (zh ? "正在生成交接摘要" : "Generating handoff summary") : null}
                {progress.stage === "completed" ? (zh ? "压缩完成" : "Compaction complete") : null}
                {progress.stage === "failed" ? (zh ? "压缩失败" : "Compaction failed") : null}
              </strong>
              {progress.error ? ` · ${progress.error}` : ""}
            </p>
          ) : null}
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
