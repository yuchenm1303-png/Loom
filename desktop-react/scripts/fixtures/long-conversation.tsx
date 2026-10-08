import { useState } from "react";
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { Transcript } from "../../src/components/Transcript";
import { TranscriptScrollController } from "../../src/components/TranscriptScrollController";
import type { TranscriptItem } from "../../src/types/loom";
import "../../src/renderer-styles";
import "../../src/customScrollbars";
import "../../src/pointerContrast";
import "../../src/pointerMotion";

document.documentElement.dataset.loomTheme = "light";
const params = new URLSearchParams(location.search);
document.documentElement.dataset.loomReducedMotion = params.get("motion") === "1" ? "false" : "true";
const paragraphs = Number(params.get("sections") || 16);
const noop = () => {};
const response = Array.from({ length: paragraphs }, (_, section) => `## 配置步骤 ${section}

这是一段较长的历史回答，包含部署说明、配置检查和执行结果。请保留已有的数据和界面行为。${"仔细检查每一项结果，然后继续下一步。".repeat(12)}

- 检查登录配置并确认回调地址。
- 重新部署后验证浏览器显示结果。
- 保存执行记录并检查返回状态。

| 项目 | 状态 | 备注 |
| --- | --- | --- |
${Array.from({ length: 6 }, (_, index) => `| 配置 ${index} | 已完成 | 记录检查结果 |`).join("\n")}

\`\`\`typescript
${Array.from({ length: 8 }, (_, line) => `const value${line} = "${"long output ".repeat(12)}";`).join("\n")}
\`\`\`
`).join("\n\n");
const history: TranscriptItem[] = Array.from({ length: 20 }, (_, turn) => [
  { id: `user-${turn}`, turnId: `turn-${turn}`, type: "user_message", text: `检查配置 ${turn}` },
  { id: `answer-${turn}`, turnId: `turn-${turn}`, type: "assistant_message", phase: "final_answer",
    status: "completed", text: `# 历史回答 ${turn}\n\n${response}\n\n结束标记 ${turn}` },
]).flat() as TranscriptItem[];
function Fixture() {
  const [items, setItems] = useState(history);
  const [running, setRunning] = useState(false);
  const [thread, setThread] = useState("long");
  const [draft, setDraft] = useState("");
  Object.assign(window, { longConversation: {
    start() {
      setRunning(true);
      setItems([...history, { id: "live-user", turnId: "live", type: "user_message", text: "继续检查" },
        { id: "live-answer", turnId: "live", type: "assistant_message", status: "streaming", text: "开始" }]);
    },
    delta(index: number) { setItems(previous => previous.map(item => item.id === "live-answer"
      ? { ...item, text: `${item.text}\n\n流式更新 ${index}。` } : item)); },
    finish() { setRunning(false); },
    prepend() { setItems(previous => [
      { id: "older-user", turnId: "older", type: "user_message", text: "更早的记录" },
      { id: "older-answer", turnId: "older", type: "assistant_message", text: response, status: "completed" },
      ...previous,
    ]); },
    small() { setThread("small"); setRunning(false); setItems([{ id: "small", type: "assistant_message", text: "Small conversation" }]); },
    restore() { setThread("long"); setItems(history); },
  } });
  return <div className="workspace" style={{ position: "fixed", inset: 0, display: "block" }}>
    <div className="conversation-stage" style={{ height: "calc(100% - 70px)" }}>
      <Transcript items={items} running={running} promptDisabled={running} currentTurnId={running ? "live" : null} onApproval={noop} />
      <TranscriptScrollController items={items} threadId={thread} running={running} currentTurnId={running ? "live" : null} />
    </div>
    <textarea aria-label="Message" value={draft} onChange={event => setDraft(event.target.value)} style={{ width: "100%", height: 70 }} />
  </div>;
}
createRoot(document.getElementById("root")!).render(<I18nProvider><Fixture /></I18nProvider>);
