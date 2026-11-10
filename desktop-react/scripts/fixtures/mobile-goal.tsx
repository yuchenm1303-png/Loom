/** The phone's long-goal card alone, so it can be looked at without the whole phone page.
 *
 *   ?reviewed=1  a review round came back (an unmet criterion, a review criterion)
 *   ?lines=1     parallel lines are running
 *   ?paused=1    the goal is paused
 *   ?records=1   the 记录 page of a goal: its notes, claims ledger and reviews
 *   ?depth=1     a research goal mid-way: criteria the lead marked met, its chosen direction, a mid-course review
 */
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";
import "../../src/mobile/mobile.css";
import { MobileGoalCard } from "../../src/mobile/MobileGoalCard";
import { GoalRecords } from "../../src/mobile/GoalRecords";
import { toGoal, type LongGoal } from "../../src/components/projectOverview";

window.localStorage.setItem("loom.settings.language", "zh-CN");
applyThemePreference("dark", { animate: false, syncNative: false });
const params = new URLSearchParams(window.location.search);
const reviewed = params.has("reviewed");
const depth = params.has("depth");

const raw: LongGoal = {
  objective: "在 E:\\ai run 的 5G NR PUSCH 神经接收机研究基础上，新增 AI 信道估计器支路：用神经网络替代 LS 信道估计，送入 LMMSE 均衡器，paired 评估 BLER，isolating channel-estimation gain。同时继续深挖神经接收机（U-Net/注意力架构、per-SNR 损失）。所有实验可复现，更新 report。",
  status: params.has("paused") ? "paused" : "active", phase: "working", tokens: 179000, tokenBudget: 1_000_000_000, turns: 2,
  reason: "", summary: "", checked: false, startedAt: Date.now() / 1000 - 100 * 60, hours: 6,
  reviews: reviewed ? [{ round: 1, verdict: "needs_changes", summary: "", major: 2, minor: 1 }] : [],
  lines: params.has("lines") ? [
    { title: "AI 信道估计器训练", state: "running", tokens: 820000 },
    { title: "U-Net 架构消融", state: "done", tokens: 1_400_000, check: "passed" },
  ] : [],
  charter: {
    deliverable: "可复现的 AI-RAN 研究扩展：(1) AI 信道估计器（neural channel estimator 替代 LS，isolating CE gain）与 (2) 神经接收机架构消融（U-Net/注意力 + per-SNR 损失），paired BLER 评估 + report 更新。所有代码、checkpoint、eval JSON 在 E:\\ai run。",
    kind: "research", current: reviewed ? 5 : 0,
    phases: ["文献调研与 novelty 检查（AI 信道估计、神经接收机架构）", "AI 信道估计器实现与训练", "神经接收机架构消融", "paired BLER 评估", "report 更新与自审", ...(reviewed ? ["Revision round 1"] : [])],
    criteria: [
      { id: "C1", text: "AI 信道估计器：CNN/ResNet 输入 received grid + pilot mask，输出 complex 信道估计，MSE 优于 LS 且 paired BLER 有提升", status: "open", origin: "charter", ...(depth ? { self: "met" } : {}) },
      { id: "C2", text: "神经接收机 U-Net/注意力变体与基线 paired 对比，给出置信区间", status: reviewed ? "unmet" : "open", origin: "charter" },
      { id: "C3", text: "per-SNR 损失的消融结果写进报告", status: depth ? "open" as const : "met" as const, origin: "charter", ...(depth ? { self: "met" } : {}) },
      { id: "C4", text: "所有实验一条命令可复现", status: "open", origin: "charter" },
      { id: "C5", text: "report 更新并与 eval JSON 数字一致", status: "dropped", origin: "charter" },
      ...(reviewed ? [{ id: "C6", text: "在 12–16 dB 高 SNR 区验证 error floor", status: "open" as const, origin: "review 1" }] : []),
      ...(depth ? [{ id: "C7", text: "主结果用三个随机种子重跑并给出置信区间", status: "open" as const, origin: "early review" }] : []),
    ],
  },
  ...(depth ? { directions: { state: "chosen" as const, candidates: ["AI 信道估计器", "链路自适应联合优化", "高 SNR 鸿沟"], ranking: [1, 3, 2], chosen: 1, reason: "" },
               earlyReview: { state: "done" as const, summary: "证据单薄", reject: 2, added: ["C7"] } } : {}),
};
raw.records = {
  notes: [{ at: Date.now() / 1000 - 3600, text: "本轮完成跨信道可部署复验：CDL-A/B/C 与 30 km/h 四个条件全部落盘。\n学习型斜率极差仅 0.004/dB，温度曲线是接收机属性。" },
          { at: Date.now() / 1000 - 600, text: "按中途评审改正取整规则：ceil 在 c=16 反了 95 倍，改为四舍五入并加测试锁住。" }],
  claims: [{ claim: "CDL-A 3 km/h：可部署曲线修复 17 块、弄坏 0 块（McNemar p≈1.5e-5）", command: "python src/check_deploy.py --json results/deploy_calib.json", output: "results/deploy_calib.json points[*].sources.nr" }],
  reviews: [{ round: 0, verdict: "early_review", at: Date.now() / 1000 - 1800, summary: "理论核正确但属教科书结果；统计口径有实质错误。",
              major: ["整数 k 的取整方向说反了", "每格 3 个种子却用 60 万次查询算 z"], minor: [], requests: ["三个以上随机种子重跑并给出种子级置信区间"] }],
};
const goal = toGoal(raw)!;

createRoot(document.getElementById("root")!).render(
  <I18nProvider>
    <div className="m-stage m-build-stage" style={{ padding: 16, minHeight: "100vh", background: "var(--loom-canvas)" }}>
      <MobileGoalCard goal={goal} raw={raw}>
        <div className="m-actions"><button type="button" className="m-button">{raw.status === "paused" ? "继续目标" : "暂停目标"}</button></div>
      </MobileGoalCard>
      {params.has("records") ? <div style={{ marginTop: 24 }}><GoalRecords goal={raw} /></div> : null}
    </div>
  </I18nProvider>,
);
