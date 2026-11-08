import { StrictMode, useLayoutEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { AnimatedFold } from "../../src/motion/AnimatedFold";
import { MotionRows, MotionValue } from "../../src/mobile/MotionRows";
import { GoalBudgetControl } from "../../src/components/GoalBudgetControl";
import { AttachmentTray } from "../../src/mobile/AttachmentTray";
import type { AttachmentQueue, MobileAttachment } from "../../src/mobile/attachmentQueue";
import "../../src/renderer-styles";
import "../../src/mobile/mobile.css";
document.documentElement.dataset.loomSurface = "mobile";
document.documentElement.dataset.loomReducedMotion = "false";
document.documentElement.dataset.loomTheme = "dark";
const file = (id: number): MobileAttachment => ({ id, file: new File(["fixture"], id === 1 ? "image-1.svg" : `document-${id}.txt`, { type: id === 1 ? "image/svg+xml" : "text/plain" }), status: "ready", path: `D:/attachments/${id}.txt`, ...(id === 1 ? { preview: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='80' height='80'%3E%3Crect width='80' height='80' fill='%23a797f5'/%3E%3C/svg%3E" } : {}) });
function Audit() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("保留草稿");
  const [rows, setRows] = useState([{ id: "a", state: "运行中" }, { id: "b", state: "运行中" }]);
  const [files, setFiles] = useState([file(1), file(2)]);
  const [goal, setGoal] = useState({ tokens: 2400000, tokenBudget: 4000000, startedAt: 1 });
  useLayoutEffect(() => { Object.assign(window, { motionAudit: {
    fold: (next: boolean) => flushSync(() => setOpen(next)),
    rows: (next: typeof rows) => flushSync(() => setRows(next)),
    files: (ids: number[]) => flushSync(() => setFiles(ids.map(file))),
  } }); }, []);
  const queue = { retry() {}, remove(id: number) { Reflect.set(window, "removedAttachment", id); setFiles(current => current.filter(item => item.id !== id)); } } as unknown as AttachmentQueue;
  return <main style={{ overflow: "auto", height: "100dvh", padding: 16, boxSizing: "border-box" }}>
    <h2>动画验收</h2><span className="m-dot is-run" />
    <button id="fold-toggle" className="m-button" onClick={() => setOpen(current => !current)}>展开与收起</button>
    <div id="audit-fold"><AnimatedFold open={open}><section className="m-card">
      <label className="m-field">草稿<input value={value} onChange={event => setValue(event.target.value)} /></label>
      <p>{"内容自然展开，文字保持清晰。".repeat(8)}</p>
    </section></AnimatedFold></div>
    <div id="audit-rows"><MotionRows items={rows} itemKey={row => row.id}>{row => <article className="m-agent-card" data-id={row.id}><div><h3>代理 {row.id}</h3><p><MotionValue value={row.state}>{row.state}</MotionValue></p></div></article>}</MotionRows></div>
    <GoalBudgetControl goal={goal} canResume onSubmit={async (_action, update) => { setGoal(current => ({ ...current, tokenBudget: update.tokenBudget })); return true; }} />
    <AttachmentTray items={files} queue={queue} online disabled={false} />
  </main>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Audit /></StrictMode>);
