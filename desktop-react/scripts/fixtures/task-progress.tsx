import React, { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { Transcript } from "../../src/components/Transcript";
import { TranscriptScrollController } from "../../src/components/TranscriptScrollController";
import type { TranscriptItem } from "../../src/types/loom";
import "../../src/styles.css";
import "../../src/theme.css";
import "../../src/components/run-progress.css";

const root = createRoot(document.getElementById("root")!);
(window as unknown as { renderPlan(width: number, completed?: boolean, nextTurn?: boolean): void }).renderPlan = (width, completed = false, nextTurn = false) => {
  const turnId = nextTurn ? "turn-2" : "turn-1";
  const items: TranscriptItem[] = [
    { id: "user", threadId: "thread", turnId, type: "user_message", status: "completed", text: "完成测试与报告" },
    { id: "plan", threadId: "thread", turnId, type: "tool_call", status: "completed", ok: true, toolName: "update_plan", result: { plan: [
      { step: "确认浏览器版本", status: "completed" },
      { step: "运行压力测试，验证长步骤在窄屏下换行并保留完整文字", status: completed ? "completed" : "in_progress" },
      { step: "补测连接", status: "blocked", blocker: "当前环境没有配置 CDP；记录未覆盖" },
      ...Array.from({ length: 12 }, (_, i) => ({ step: `交付检查 ${i + 1}`, status: "pending" })),
    ] } },
    { id: "text", threadId: "thread", turnId, type: "assistant_message", status: "completed", phase: "commentary", text: Array.from({ length: 30 }, (_, i) => `第 ${i + 1} 段正文。浏览器检查正在进行。`).join("\n\n") },
  ];
  flushSync(() => root.render(<StrictMode>
    <div className="conversation-stage" style={{ width, height: 650, maxWidth: "100vw" }}>
      <style>{"body { margin: 0; } .conversation-stage { grid-template-rows: auto minmax(0, 1fr); } .transcript-scroll { overflow-y: auto; }"}</style>
      <div className="run-progress-frame top">Loom 正在工作</div>
      <Transcript items={items} running currentTurnId={turnId} onApproval={() => {}} />
      <TranscriptScrollController items={items} running threadId="thread" currentTurnId={turnId} />
    </div>
  </StrictMode>));
};
