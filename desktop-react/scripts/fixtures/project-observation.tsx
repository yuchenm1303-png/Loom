import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ProjectFlow, type FlowData } from "../../src/components/ProjectFlow";
import type { BuildInspection } from "../../src/components/buildInspection";
import { applyThemePreference } from "../../src/theme";
import { I18nProvider } from "../../src/i18n";
import "../../src/renderer-styles";

const params = new URLSearchParams(location.search);
applyThemePreference(params.get("theme") === "light" ? "light" : "dark", { animate: false, syncNative: false });
const snapshot = (thread: string, goal = false): BuildInspection => ({
  observedAt: Date.now() / 1000, source: goal ? "goal" : "project", runId: thread, revision: 3, freshness: "stale",
  goal: goal ? { objective: "研究验证方法", status: "active", phase: "working", tokens: 1200, tokenBudget: 9000, turns: 3, checked: false, reason: "", summary: "",
    lines: [{ title: "验证导频密度", state: "waiting", tokens: 800, cap: 4000, retryAt: Date.now() / 1000 + 60, why: "模型服务不可用", check: "not_run", changed: ["notes/pilot.md"], report: "等待补齐实验" }] } : null,
  waits: goal ? [] : [{ id: "scope", runId: thread, code: "fileScope", since: Date.now() / 1000 - 130, detail: "导入测试等待解析器释放共享文件" }],
  criteria: goal ? [] : [{ id: "parser:1", taskId: "parser", expectation: `${thread} · CSV 导入保持金额精度`, status: "recorded_pass",
    evidence: { eventId: "event-real-1", sessionId: "reviewer-1", candidateId: "candidate-before-edit", verdict: "passed", command: "python -m pytest tests/test_parser.py",
      observed: "exit=0, expected=0; source matches at check boundaries: True", log: "exit=0\nstdout:\n12 passed", exitCode: 0, source: "runtime_exit_code", recordedAt: new Date().toISOString(), subjectPaths: ["ledger/parser.py"] } },
    { id: "tests:1", taskId: "tests", expectation: "多格式导入回归", status: "not_run" }],
  tasks: goal ? [] : [{ id: "parser", title: "实现 CSV 解析与金额校验", state: "verified", dependsOn: [], fileScopes: ["ledger/parser.py"], runId: "child-parser" },
    { id: "tests", title: "补齐集成回归", state: "pending", dependsOn: ["parser"], fileScopes: ["tests"], runId: "child-tests" }],
  reviews: goal ? [] : [{ id: "r1", runId: thread, scope: "project", verdict: "needs_changes", summary: "金额校验通过，但空文件导入没有覆盖。",
    candidateId: "candidate-before-edit", checks: [], issues: [{ taskId: "tests", description: "补充空文件输入的回归检查" }] }],
  changes: goal ? [] : [{ runId: thread, baseCommit: "abc123456789", files: [{ status: "M", path: "ledger/parser.py" }, { status: "?", path: "tests/金额 校验.py" }], total: 2, error: "" }],
  metrics: goal ? null : { span: 400, peak: 2, concurrency: { "1": 100, "2": 300 }, phases: { working: { seconds: 400, tokens: 2500, episodes: 2 }, projectReview: { seconds: 200, tokens: 1000, episodes: 1 } }, waits: { fileScope: 130 } },
  budget: goal ? undefined : { used: 3500, earlierMilestones: 1000, limit: 10000, position: { knownTokens: 3200, inFlightTokens: 200, assumedUnknownTokens: 100, unknownRequests: 1, chargedTokens: 3500, hardBillingCap: false } },
  deliveries: goal ? [] : [{ runId: thread, state: "integrated", branch: "refs/heads/main", verification: "passed", integration: "integrated", reason: "结果已集成到本地分支；远端发布仍待处理。",
    receipt: { commit: "abc123456789deadbeef", ref: "refs/heads/codex/project-build", status: "delivered", integration: "integrated", publication: "pending" } }], recoveries: { review_evidence_rejected: 1 },
});
function Fixture() {
  const [thread, setThread] = useState("build-a");
  const [offline, setOffline] = useState(false);
  const [goal, setGoal] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const flags = ((window as unknown as { observationFlags?: { fail: boolean; stale: boolean; calls: string[]; delayed: boolean; releases: (() => void)[] } }).observationFlags ??=
    { fail: false, stale: false, calls: [], delayed: false, releases: [] });
  Object.assign(window, { observationFixture: { offline: setOffline, thread: setThread, goal: setGoal, unavailable: setUnavailable } });
  const data: FlowData = { phase: "live", objective: "构建账本导入工具", narration: { summary: "已工作 12 分钟", text: "正在补齐集成回归" }, milestone: 1, state: "needs_you", model: "test",
    observation: { observedAt: Date.now() / 1000, offline }, attention: { reason: "复审要求补充证据", action: "recover" },
    repository: { name: "ledger", path: "C:/work/ledger", commit: "abc1234", subject: "项目起点", branch: "main" }, scope: [{ path: "ledger/", count: 1 }],
    knowledge: { checks: 1, notes: 0, latest: "" }, budget: { tokens: 4500, tokenLimit: 10000 }, workers: 2, maxWorkers: 8, controls: { pause: false, resume: false, cancel: true },
    tasks: [{ id: "parser", title: "实现 CSV 解析与金额校验", state: "integrated" }, { id: "tests", title: "补齐集成回归", state: "pending", dependsOn: ["parser"] }],
    conversation: [{ id: "reply", role: "assistant", text: "解析器已实现，整体复审发现空文件输入缺少回归检查。请查看右侧证据与返工记录。" }], agents: [],
  };
  return <div className="project-mode-stage" style={{ height: "100vh" }}><ProjectFlow data={data} conversationId={thread} lang={params.get("lang") === "en" ? "en" : "zh"} actions={{
    onChat: () => {}, onAttention: () => {}, onInspect: unavailable ? undefined : async () => {
      const result = snapshot(thread, goal);
      if (flags.stale) result.observedAt -= 60;
      flags.calls.push(thread);
      if (flags.delayed) await new Promise<void>(resolve => flags.releases.push(resolve));
      if (flags.fail) throw new Error("Host 暂时不可用");
      return result;
    },
  }} /></div>;
}
createRoot(document.getElementById("root")!).render(<I18nProvider><Fixture /></I18nProvider>);
