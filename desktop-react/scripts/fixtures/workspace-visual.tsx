// Real App and production stylesheet order, with an isolated in-memory host.
// No account, filesystem or model request can escape this fixture.
const params = new URLSearchParams(location.search);
const theme = params.get("theme") || "light";
localStorage.removeItem("loom.inspector.open");
localStorage.setItem("loom.settings.language", params.get("lang") || "zh-CN");
localStorage.setItem("loom.settings.desktop.v2", JSON.stringify({ appearance: {
  theme, scale: "100", density: params.get("density") || "comfortable",
  reducedMotion: params.get("motion") !== "1",
} }));
const workspace = "C:/Workspace/Loom";
const at = "2026-10-07T12:00:00Z";
const projects = [
  { id: "loom", name: "Loom", root: workspace, threadCount: 6 },
  { id: "agent", name: "loom-agent-test", root: "C:/Workspace/agent", threadCount: 2 },
  { id: "relay", name: "termrelay", root: "C:/Workspace/relay", threadCount: 3 },
];
const titles = ["配置第三方登录与邮件验证", "修复模型面板卡片错位", "优化图片附件发送", "检查项目面板浅色主题", "优化审查面板", "确认 Computer Use 能力", "测试代理协作", "浏览器交互验证", "排查浏览器控制", "整理对话主题", "中转站快捷登录"];
const threads = titles.map((title, index) => ({
  id: `visual-${index}`, projectId: index < 6 ? "loom" : index < 8 ? "agent" : "relay",
  title, customTitle: true, workspace, permissionMode: "full-access", status: "idle",
  createdAt: at, updatedAt: at,
}));
const decision = {
  id: "setup", title: "选择配置方式", description: "登录与邮件验证的准备工作已完成，选择接下来的协作方式。",
  multiple: params.get("multiple") === "1", allowCustomInput: true,
  options: [
    { id: "guided", title: "在浏览器里手动操作，我实时指导", recommended: true,
      description: "逐步完成邮件域名验证、Google OAuth 和 GitHub OAuth 配置。每一步都有清晰的填写说明。" },
    { id: "manual", title: "生成完整配置手册与字段表",
      description: "按手册独立完成设置。包含 DNS 记录、回调地址和环境变量模板，可随时回到这里补充问题。" },
  ],
};
const items = [
  { id: "user", type: "user_message", text: "帮我配置 Loom 的第三方登录与邮件验证。" },
  { id: "answer", type: "assistant_message", phase: "final_answer", text: "配置材料已经准备好。\n\n```loom-decision\n" + JSON.stringify(decision) + "\n```" },
  { id: "file", type: "file_edit", paths: ["docs/login-setup.md"], diff: "+# 登录配置\n+域名验证\n+OAuth 回调地址\n-旧配置", status: "completed" },
].map(item => ({ ...item, threadId: "visual-0", turnId: "turn-1", createdAt: at }));
const profile = { selection: "builtin:minimax", id: "minimax-m3", kind: "builtin", name: "MiniMax-M3",
  adapter: "openai-compatible", model: "MiniMax-M3", configured: true, vision: true };
const models = { primary: profile, profiles: [profile], activeModelId: profile.id,
  current: { ...profile, provider: profile.adapter }, recentModels: [] };
const navigation = {
  held: new Set<string>(),
  reads: [] as string[],
  pending: new Map<string, { resolve(value: unknown): void; reject(error: Error): void }>(),
  hold(id: string) { this.held.add(id); },
  release(id: string) {
    this.held.delete(id);
    this.pending.get(id)?.resolve(threadSnapshot(id));
    this.pending.delete(id);
  },
  fail(id: string) {
    this.held.delete(id);
    this.pending.get(id)?.reject(new Error(`Read failed: ${id}`));
    this.pending.delete(id);
  },
};
Object.assign(window, { navigationFixture: navigation });
function threadSnapshot(id: string) {
  return { thread: threads.find(thread => thread.id === id) || threads[0],
    turns: params.get("empty") === "1" ? [] : [{ id: "turn-1", threadId: id, status: "completed",
      startedAt: at, completedAt: at, finalItemId: "answer", items: items.map(item => ({ ...item, threadId: id,
        ...(params.get("navigation") === "1" && item.type === "assistant_message" ? { text: `Loaded ${id}` } : {}) })) }] };
}
const handlers: Record<string, (args: Record<string, unknown>) => unknown> = {
  "project/workspace_status": args => ({ projectId: args.projectId, root: workspace, exists: true, isDirectory: true,
    git: { available: true, isRepo: false, branch: "", summary: "", changedCount: 0, changedFiles: [], truncated: false },
    tree: { entries: [], truncated: false, limit: 100, maxDepth: 2 } }),
  "profile/insights": () => ({
    range: { startDate: "2025-10-01", endDate: "2026-10-07", totalTokens: 0 },
    totals: { activeDays: 0, modelCalls: 0 }, streaks: { current: 0 }, peakDay: null,
    days: [],
  }),
  "thread/list": () => ({ threads, counts: { active: threads.length, archived: 50, all: threads.length + 50 } }),
  "project/list": () => ({ projects, unfiledThreadCount: 0 }),
  "thread/read": args => {
    const id = String(args.threadId);
    navigation.reads.push(id);
    if (navigation.held.has(id)) return new Promise((resolve, reject) => navigation.pending.set(id, { resolve, reject }));
    return threadSnapshot(id);
  },
  "thread/context": () => ({ context: null }),
  "turn/start": args => {
    document.body.dataset.submitted = String(args.input || args.text || JSON.stringify(args));
    return { turn: { id: "turn-2", threadId: "visual-0", status: "running", items: [] } };
  },
};
const bridge: Record<string, unknown> = {
  connect: async () => ({ protocolVersion: 1, serverInfo: { name: "visual-fixture", version: "0" }, capabilities: {},
    runtime: { model: profile.name, defaultWorkspace: workspace, defaultPermissionMode: "full-access",
      permissionModes: ["read-only", "approval", "workspace", "full-access"] } }),
  call: async (method: string, args: Record<string, unknown> = {}) => handlers[method]?.(args) || {},
  listModels: async () => models,
  accountStatus: async () => ({ ok: true, snapshot: { configured: true, reachable: true, authenticated: true,
    user: { id: "visual-user", name: "Loom Designer", email: "visual@example.invalid" }, serviceUrl: "" } }),
  onNotification: () => () => undefined,
  setZoomFactor: (factor: number) => factor,
  setNativeTheme: async (source: string) => source === "dark" ? "dark" : "light",
  filePathFor: () => "",
};
(window as unknown as { loom: unknown }).loom = new Proxy(bridge, {
  get: (target, key: string) => key in target ? target[key] : async () => ({}),
});
void import("../../src/main");
