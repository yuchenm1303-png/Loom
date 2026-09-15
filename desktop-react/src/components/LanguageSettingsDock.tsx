import { Globe2 } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { LOOM_LANGUAGES, useI18n, type LoomLanguage } from "../i18n";
import "./language-settings-dock.css";

const STATIC_SETTING_TRANSLATIONS: Record<string, string> = {
  "Back to Loom": "返回应用",
  "Settings": "设置",
  "Local agent controls": "本地 Agent 控制中心",
  "Search settings": "搜索设置",
  "Loom runtime": "Loom 运行时",
  "Ready for changes": "可以修改设置",
  "Turn active": "任务运行中",
  "General": "常规",
  "Appearance": "外观",
  "Models": "模型",
  "Capabilities": "能力",
  "Computer Use": "电脑操控",
  "Browser": "浏览器",
  "Terminal": "终端",
  "Plugins": "插件",
  "MCP": "MCP",
  "Skills": "技能",
  "Permissions": "权限",
  "Keyboard shortcuts": "键盘快捷键",
  "Privacy & data": "隐私与数据",
  "Developer": "开发者",
  "Integrations": "集成",
  "Connectors": "连接器",
  "External services": "外部服务",
  "Connect accounts once, verify health, and expose authenticated service tools without putting secrets in Loom settings.": "连接账号、检查状态并使用已授权的服务工具，无需在 Loom 设置中保存密钥。",
  "Refresh": "刷新",
  "Connected": "已连接",
  "Not connected": "未连接",
  "Finish the active turn before changing authorization.": "请在当前任务结束后再更改授权。",
  "Connector identity is frozen into each sampled Step, so Loom never swaps accounts underneath an action already being reviewed or executed.": "每一步都会固定连接器身份，正在审核或执行的操作不会被切换到另一账号。",
  "Connector action failed": "连接器操作失败",
  "New model Steps will use the updated connector binding.": "后续任务步骤将使用更新后的连接器授权。",
  "Repositories, files, code search, issues, pull requests, branches, and Actions runs.": "访问仓库、文件、代码搜索、议题、拉取请求、分支和 Actions 运行记录。",
  "Connect GitHub": "连接 GitHub",
  "Authenticated GitHub tools are available to new agent Steps.": "后续任务步骤可以使用已授权的 GitHub 工具。",
  "Use browser login, import an existing gh session, or store a personal access token in the OS keychain.": "通过浏览器登录、导入现有 gh 登录状态，或把个人访问令牌存入系统凭据库。",
  "Browser sign-in": "浏览器登录",
  "Uses Loom's GitHub device OAuth client when configured; otherwise falls back to authenticated GitHub CLI web login.": "配置了 Loom 的 GitHub OAuth 客户端时使用设备授权，否则使用 GitHub CLI 浏览器登录。",
  "Connect": "连接",
  "Reconnect": "重新连接",
  "Import GitHub CLI session": "导入 GitHub CLI 登录状态",
  "Reuse the account already authorized by `gh auth login`. Loom copies the token into its own OS-keychain entry.": "使用已经通过 `gh auth login` 授权的账号。Loom 会把凭证复制到自己的系统凭据库。",
  "GitHub CLI was not detected on this machine.": "本机未检测到 GitHub CLI。",
  "Import gh": "导入 gh",
  "Personal access token": "个人访问令牌",
  "The token is validated once and stored in the OS credential vault. It is never persisted in settings.json or connectors.json.": "令牌会在验证后存入系统凭据库，不会写入 settings.json 或 connectors.json。",
  "Save": "保存",
  "GitHub personal access token": "GitHub 个人访问令牌",
  "Authorization in progress": "正在授权",
  "Finish the GitHub confirmation, then Loom will pick up the connected account automatically.": "完成 GitHub 授权后，Loom 会自动识别已连接的账号。",
  "Mode": "方式",
  "Verification": "验证",
  "Device code": "设备验证码",
  "Copied by GitHub CLI": "已由 GitHub CLI 复制",
  "Copy code": "复制验证码",
  "Use this code on GitHub's device page.": "在 GitHub 设备授权页面输入此验证码。",
  "Connection details": "连接详情",
  "Only non-secret metadata is shown here or included in diagnostics.": "此处和诊断信息只显示不含密钥的状态。",
  "Account": "账号",
  "Credential source": "凭据来源",
  "None": "无",
  "OAuth scopes": "OAuth 权限范围",
  "Not reported": "未上报",
  "Binding": "授权绑定",
  "Process-local credential identity; it is not the token.": "进程内的凭据标识，不是令牌本身。",
  "Browser device OAuth": "浏览器设备 OAuth",
  "Configured": "已配置",
  "Uses GitHub CLI fallback when available": "可用时使用 GitHub CLI 备用登录方式",
  "GitHub CLI": "GitHub CLI",
  "Available": "可用",
  "Not detected": "未检测到",
  "Last check": "最近检查",
  "Safety boundary": "安全边界",
  "Connector authentication does not bypass Loom's execution or approval model.": "连接器授权不会绕过 Loom 的执行权限或审批规则。",
  "Read operations stay read-only": "读取操作保持只读",
  "Repository/file/search/issue/PR/Actions reads are classified as read-only tools.": "读取仓库、文件、搜索、议题、拉取请求和 Actions 的工具均归类为只读。",
  "Writes remain approval-aware": "写入操作遵循审批规则",
  "Creating issues/comments/branches/PRs or committing files is SENSITIVE and crosses the normal Loom approval boundary.": "创建议题、评论、分支、拉取请求或提交文件属于敏感操作，须经过 Loom 的常规审批。",
  "Account identity is Step-bound": "账号身份随步骤固定",
  "Changing or disconnecting an account only affects a future model Step. Existing sampled actions cannot be retargeted.": "更改或断开账号只影响后续步骤，已开始的操作不会切换账号。",
  "Disconnect GitHub from Loom": "断开 Loom 的 GitHub 连接",
  "This removes Loom's keychain credential and creates an explicit disconnect marker. It does not sign out GitHub CLI or delete environment variables.": "这会删除 Loom 凭据库中的授权并标记为断开，不会退出 GitHub CLI 或删除环境变量。",
  "Disconnect": "断开连接",
  "Credential lifecycle": "凭据生命周期",
  "GitHub token rotation is handled before a future model Step is sampled; existing Step bindings are never retargeted.": "GitHub 令牌在后续任务步骤开始前更新；已开始的步骤不会切换授权。",
  "Authentication": "认证方式",
  "GitHub device OAuth": "GitHub 设备 OAuth",
  "Connected credential": "已连接的凭据",
  "Access and refresh secrets stay in the OS credential vault.": "访问令牌和刷新令牌保存在系统凭据库中。",
  "Automatic rotation": "自动更新",
  "Ready": "可用",
  "GitHub authorization returned no status.": "GitHub 授权未返回状态。",
  "GitHub connected.": "GitHub 已连接。",
  "GitHub browser authorization started. The one-time code was copied to your clipboard by GitHub CLI.": "GitHub 浏览器授权已开始。GitHub CLI 已把一次性验证码复制到剪贴板。",
  "GitHub browser authorization started. Complete the device confirmation in your browser.": "GitHub 浏览器授权已开始。请在浏览器中完成设备确认。",
  "Paste a GitHub personal access token first.": "请先输入 GitHub 个人访问令牌。",
  "GitHub token validated and stored in the OS credential vault.": "GitHub 令牌已验证并存入系统凭据库。",
  "Device code copied.": "设备验证码已复制。",
  "Could not copy the device code.": "无法复制设备验证码。",
  "GitHub CLI authorization imported into Loom.": "GitHub CLI 授权已导入 Loom。",
  "GitHub disconnected from Loom.": "Loom 的 GitHub 连接已断开。",
  "Reconnect required when expired": "过期后需要重新连接",
  "Not required": "无需更新",
  "Loom refreshes inside a five-minute safety window.": "Loom 会在到期前五分钟内更新令牌。",
  "PAT, environment, and GitHub CLI credentials use their own lifetime.": "PAT、环境变量和 GitHub CLI 凭据各有自己的有效期。",
  "Access token": "访问令牌",
  "Refresh token": "刷新令牌",
  "The next Step rotates this credential before it reaches the safety window.": "后续步骤会在安全期限内更新该凭据。",
  "Reconnect GitHub after the refresh credential itself expires.": "刷新凭据过期后，请重新连接 GitHub。",
  "Renewal warning": "更新警告",
  "Automatic renewal is active.": "自动更新已启用。",
  "Loom rotates the keychain-backed GitHub access token without writing secret material to connector JSON state.": "Loom 会更新系统凭据库中的 GitHub 访问令牌，不会把密钥写入连接器 JSON 状态。",
  "This device authorization is not refreshable.": "此设备授权无法自动更新。",
  "GitHub did not issue a refresh token for this application configuration; reconnect when the access token expires.": "GitHub 未为此应用配置签发刷新令牌；访问令牌过期后请重新连接。",
  "Advanced": "高级",
  "Loom desktop": "Loom 桌面端",
  "Core defaults, runtime health, and the fastest routes to the settings that shape every agent run.": "集中管理影响每次 Agent 运行的默认值、运行时健康状态和关键设置入口。",
  "Manage the defaults and presentation of your local agent workspace from one place.": "统一管理本地 Agent 工作区的默认行为和界面显示。",
  "Runtime ready": "运行时就绪",
  "Agent turn active": "Agent 任务运行中",
  "Loom is working": "Loom 正在工作",
  "Local runtime ready": "本地运行时已就绪",
  "Your local runtime is ready": "本地运行时已就绪",
  "Model, permission boundary, exposed tools, and capability state at a glance.": "快速查看模型、权限边界、暴露工具和能力状态。",
  "One view of the model, permission boundary, exposed tools, and interface preferences currently shaping new agent work.": "集中查看当前影响新任务的模型、权限边界、暴露工具和界面偏好。",
  "Model": "模型",
  "Permission": "权限",
  "Tools": "工具",
  "Tools exposed": "暴露工具",
  "Runtime defaults": "运行默认值",
  "Defaults currently reported by the App Server for new conversations.": "App Server 当前为新对话上报的默认配置。",
  "The values Loom starts with when you create a new conversation.": "新建对话时 Loom 默认采用的配置。",
  "Default workspace": "默认工作区",
  "Used when a new conversation starts without an explicit project.": "未指定项目时，新对话使用此工作区。",
  "New conversations begin here when no project is selected.": "未选择项目时，新对话会从这里开始。",
  "Copy path": "复制路径",
  "Default permission": "默认权限",
  "Inspect the execution boundary used for sensitive actions.": "查看敏感操作采用的执行边界。",
  "Review the execution boundary used for sensitive actions.": "查看敏感操作采用的执行边界。",
  "Current model": "当前模型",
  "Switch the active inference profile and inspect providers.": "切换当前推理配置并查看模型提供方。",
  "Open model profiles and inspect the active inference route.": "打开模型配置，检查当前推理线路。",
  "Quick access": "快捷入口",
  "The controls most likely to change how Loom behaves.": "最常影响 Loom 行为的关键设置。",
  "Scale, density, motion, and code typography": "缩放、密度、动画和代码字体",
  "Choose the tool families Loom can expose": "选择 Loom 可以暴露的工具能力",
  "Diagnostics": "诊断",
  "Runtime, integrations, and raw health snapshot": "运行时、集成和原始健康快照",
  "Presentation": "显示",
  "Make Loom comfortable on your display without changing agent behavior.": "在不改变 Agent 行为的前提下优化界面阅读体验。",
  "Interface": "界面",
  "Changes apply immediately and persist across restarts.": "修改会立即生效，并在重启后保留。",
  "Interface scale": "界面缩放",
  "Scale the complete desktop UI for comfortable reading.": "缩放整个桌面界面以获得更舒适的阅读体验。",
  "Scale the entire desktop surface for more comfortable reading.": "缩放整个桌面界面，方便阅读。",
  "Content density": "内容密度",
  "Choose tighter activity rows or roomier spacing.": "选择更紧凑或更宽松的界面间距。",
  "Comfortable": "舒适",
  "Compact": "紧凑",
  "Language": "语言",
  "Switch the display language used across Loom Desktop.": "切换 Loom 桌面端的显示语言。",
  "Reduce motion": "减少动画",
  "Minimize decorative transitions, pulses, and status animation.": "减少装饰性过渡、脉冲和状态动画。",
  "Minimize decorative transitions, pulses, and animated status effects.": "减少装饰性过渡、脉冲和状态动画。",
  "Code appearance": "代码外观",
  "Monospace settings affect code, terminal output, and technical values.": "等宽字体设置会作用于代码、终端输出和技术数值。",
  "Code font": "代码字体",
  "Use the system monospace stack or a font installed on this machine.": "使用系统等宽字体或本机已安装字体。",
  "Code font size": "代码字号",
  "Adjust terminal and code readability independently.": "单独调整代码与终端的可读性。",
  "Inference": "推理",
  "Inspect and switch the active model profile used for new agent steps.": "查看并切换后续 Agent 步骤使用的模型配置。",
  "Active model": "当前模型",
  "Current": "当前",
  "Runtime model": "运行时模型",
  "Saved profiles": "已保存配置",
  "Switching is disabled while an agent turn is running.": "Agent 任务运行期间不可切换模型。",
  "Active": "当前",
  "Default endpoint": "默认端点",
  "Set active": "设为当前",
  "Switching…": "正在切换…",
  "Profiles configured in Loom's model manager.": "Loom 模型管理器中配置的模型档案。",
  "No saved model profiles.": "暂无已保存模型配置。",
  "Agent runtime": "Agent 运行时",
  "Choose which major tool families Loom can expose to the model.": "选择 Loom 可以向模型暴露的主要工具能力。",
  "Choose which major tool families Loom should expose. Preference switches and backend readiness are shown separately so disabled dependencies are not confused with a closed switch.": "选择 Loom 要向模型暴露哪些主要工具能力。偏好开关和后端就绪状态会分开显示。",
  "Finish or stop the active turn first.": "请先完成或停止当前任务。",
  "Tool exposure cannot change mid-execution.": "任务执行期间不能修改工具暴露范围。",
  "Agent capabilities": "Agent 能力",
  "Preference and runtime readiness are intentionally shown separately.": "偏好开关与运行时就绪状态会分开显示。",
  "Off": "关闭",
  "On": "开启",
  "Runtime missing": "运行时缺失",
  "Backend missing": "后端缺失",
  "Preference off": "偏好已关闭",
  "Preference on": "偏好已开启",
  "Preference on · runtime missing": "偏好开启 · 运行时缺失",
  "Runtime status not reported": "运行时状态未上报",
  "Runtime backend missing": "运行后端缺失",
  "Runtime backend ready": "运行后端可用",
  "Desktop integration": "桌面集成",
  "Screenshot-driven Windows control with UI Automation assistance and post-action verification.": "基于截图驱动的 Windows 控制，辅助 UI Automation 识别并在操作后校验。",
  "Runtime status": "运行状态",
  "Preference": "偏好",
  "Backend status": "后端状态",
  "Backend": "后端",
  "Operator": "操作后端",
  "Grounder": "视觉定位器",
  "Policy step": "策略执行步",
  "Observation": "观察模式",
  "Desktop preferences": "桌面偏好",
  "Durable preferences for the Computer Use runtime.": "Computer Use 运行时的持久化偏好。",
  "Verify actions": "操作后校验",
  "Prefer post-action verification before the agent moves on.": "优先在 Agent 继续前校验操作结果。",
  "Screenshot quality": "截图质量",
  "Balance grounding detail against capture and transfer cost.": "在视觉定位精度与截图开销之间平衡。",
  "Fast": "快速",
  "Balanced": "平衡",
  "High detail": "高细节",
  "Web interaction": "网页交互",
  "Control Loom's browser-use session or attach to a local Chrome/Edge instance.": "控制 Loom 浏览器会话，或连接本机 Chrome / Edge 实例。",
  "Browser runtime": "浏览器运行时",
  "Connection": "连接方式",
  "External browser": "外部浏览器",
  "Session persistence": "会话持久化",
  "Active sessions": "活动会话",
  "Browser preferences": "浏览器偏好",
  "Preferred browser": "首选浏览器",
  "Preferred desktop browser for compatible Browser Use backends.": "兼容 Browser Use 后端优先使用的桌面浏览器。",
  "Microsoft Edge": "微软 Edge",
  "Google Chrome": "Google Chrome",
  "System default": "系统默认",
  "Persist sessions": "保留会话",
  "Keep compatible browser profiles available across Loom restarts.": "在 Loom 重启后保留兼容的浏览器会话配置。",
  "Execution environment": "执行环境",
  "Durable preferences for shell-oriented integrations and command execution surfaces.": "管理面向 Shell 集成与命令执行界面的持久化偏好。",
  "Command defaults": "命令默认值",
  "Stored centrally so runtime integrations can converge on one desktop preference set.": "集中保存，供多个运行时集成共享同一套桌面偏好。",
  "Preferred shell": "首选 Shell",
  "Shell preference for integrations that support desktop defaults.": "支持桌面默认值的集成优先采用此 Shell。",
  "Text encoding": "文本编码",
  "Default text encoding preference for terminal surfaces.": "终端界面的默认文本编码偏好。",
  "Command timeout": "命令超时",
  "Preferred upper bound for foreground command integrations.": "前台命令集成的首选最长等待时间。",
  "Background processes": "后台进程",
  "Preserve managed background processes when supported by the runtime.": "运行时支持时保留受管后台进程。",
  "One preference source, multiple runtimes.": "一套偏好，多运行时共享。",
  "These values are persisted now; individual exec/browser/computer backends can adopt them without adding new UI controls later.": "这些值现已持久化；各执行后端后续可直接接入，无需重复增加界面设置。",
  "Extensions": "扩展",
  "Installed plugins": "已安装插件",
  "Plugin activation may require a runtime restart.": "插件启用状态可能需要重启运行时后生效。",
  "Loading plugins…": "正在加载插件…",
  "No plugins are installed.": "暂无已安装插件。",
  "Model Context Protocol": "Model Context Protocol",
  "Inspect MCP discovery and control whether discovered server tools can be exposed.": "查看 MCP 发现状态，并控制已发现服务工具是否可暴露。",
  "Discovery": "发现状态",
  "Runtime": "运行时",
  "Connected servers": "已连接服务",
  "Discovered tools": "已发现工具",
  "Transport": "传输方式",
  "MCP server editing is the next integration surface.": "MCP 服务编辑将是下一步集成能力。",
  "Reusable workflows": "可复用工作流",
  "Discovered skills": "已发现技能",
  "Discovery health": "发现健康度",
  "Execution safety": "执行安全",
  "Permission profiles": "权限配置",
  "Safety boundaries": "安全边界",
  "Productivity": "效率",
  "A single reference for the core desktop commands that keep agent work fast.": "集中查看提升 Agent 操作效率的核心桌面快捷键。",
  "New conversation": "新建对话",
  "Search conversations": "搜索对话",
  "Open settings": "打开设置",
  "Focus composer": "聚焦输入框",
  "Send message": "发送消息",
  "New line": "换行",
  "Stop active turn": "停止当前任务",
  "Toggle sidebar": "切换侧边栏",
  "Local data": "本地数据",
  "Keep diagnostic preferences explicit and make local storage boundaries easy to understand.": "清晰管理诊断偏好和本地数据边界。",
  "Diagnostics preferences": "诊断偏好",
  "Usage telemetry": "使用情况遥测",
  "Crash reports": "崩溃报告",
  "Local storage": "本地存储",
  "Runtime home": "运行时目录",
  "Settings store": "设置存储",
  "Renderer fallback": "渲染层回退",
  "No telemetry transport is enabled by these switches today.": "当前这些开关不会启用任何遥测上传通道。",
  "They are explicit persisted consent preferences for future diagnostics; the current Loom runtime stays local-first.": "它们只是为未来诊断能力保存的明确授权偏好；当前 Loom 仍以本地优先运行。",
  "Copy diagnostics": "复制诊断信息",
  "Registered tools": "注册工具",
  "Exposed tools": "暴露工具",
  "Settings schema": "设置配置版本",
  "Image attachments": "图片附件",
  "File attachments": "文件附件",
  "Capability schema": "能力配置版本",
  "Integration summary": "集成摘要",
  "MCP servers": "MCP 服务",
  "MCP tools": "MCP 工具",
  "Raw snapshot": "原始快照",
  "Useful when comparing renderer state with App Server state.": "用于对比渲染层与 App Server 的状态。",
};

const STATIC_SETTING_REVERSE = Object.fromEntries(
  Object.entries(STATIC_SETTING_TRANSLATIONS).map(([source, target]) => [target, source]),
) as Record<string, string>;

function translateTextNode(node: Text, language: LoomLanguage): void {
  const value = node.nodeValue ?? "";
  const trimmed = value.trim();
  if (!trimmed) return;
  const table = language === "zh-CN" ? STATIC_SETTING_TRANSLATIONS : STATIC_SETTING_REVERSE;
  const translated = table[trimmed];
  if (!translated || translated === trimmed) return;
  node.nodeValue = value.replace(trimmed, translated);
}

function translateAttributes(element: Element, language: LoomLanguage): void {
  const table = language === "zh-CN" ? STATIC_SETTING_TRANSLATIONS : STATIC_SETTING_REVERSE;
  for (const attr of ["title", "aria-label", "placeholder"] as const) {
    const value = element.getAttribute(attr);
    if (!value) continue;
    const translated = table[value.trim()];
    if (translated) element.setAttribute(attr, translated);
  }
}

function applyStaticSettingsLanguage(language: LoomLanguage): void {
  const root = document.querySelector(".settings-shell");
  if (!root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  while (walker.nextNode()) nodes.push(walker.currentNode as Text);
  for (const node of nodes) translateTextNode(node, language);
  for (const element of root.querySelectorAll("[title], [aria-label], [placeholder]")) {
    translateAttributes(element, language);
  }
}

function resolveLanguageMount(): HTMLElement | null {
  return document.querySelector<HTMLElement>(".settings-sidebar-top");
}

export function LanguageSettingsDock() {
  const { language, setLanguage, t } = useI18n();
  const [mount, setMount] = useState<HTMLElement | null>(() => resolveLanguageMount());

  useEffect(() => {
    applyStaticSettingsLanguage(language);
    const root = document.querySelector(".settings-shell");
    if (!root) return;
    const observer = new MutationObserver(() => applyStaticSettingsLanguage(language));
    observer.observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["title", "aria-label", "placeholder"] });
    return () => observer.disconnect();
  }, [language]);

  useEffect(() => {
    const syncMount = () => setMount((current) => {
      const next = resolveLanguageMount();
      return current === next ? current : next;
    });
    syncMount();
    const observer = new MutationObserver(syncMount);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  const chooseLanguage = (next: LoomLanguage) => {
    if (next === language) return;
    setLanguage(next);
    window.dispatchEvent(new CustomEvent("loom:language-updated", { detail: { language: next } }));
  };

  const control = (
    <section className="language-settings-dock" aria-label={t("settings.general.language")}>
      <Globe2 className="language-settings-icon" size={17} strokeWidth={1.7} aria-hidden="true" />
      <div className="language-settings-copy"><strong>{t("settings.general.language")}</strong><span>{t("settings.general.languageDesc")}</span></div>
      <div className="language-settings-options" role="group" aria-label={t("settings.general.language")}>
        {LOOM_LANGUAGES.map((item) => (
          <button type="button" key={item.value} className={item.value === language ? "active" : ""} onClick={() => chooseLanguage(item.value)} aria-pressed={item.value === language} title={item.label}>
            {item.value === "en" ? "EN" : "中"}
          </button>
        ))}
      </div>
    </section>
  );

  return mount ? createPortal(control, mount) : null;
}
