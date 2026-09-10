import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type LoomLanguage = "en" | "zh-CN";

type TranslationValues = Record<string, string | number | boolean | null | undefined>;

type I18nContextValue = {
  language: LoomLanguage;
  setLanguage(language: LoomLanguage): void;
  t(key: string, values?: TranslationValues): string;
};

const LANGUAGE_STORAGE_KEY = "loom.settings.language";

export const LOOM_LANGUAGES: { value: LoomLanguage; label: string; nativeLabel: string }[] = [
  { value: "en", label: "English", nativeLabel: "English" },
  { value: "zh-CN", label: "Chinese Simplified", nativeLabel: "简体中文" },
];

const TRANSLATIONS: Record<LoomLanguage, Record<string, string>> = {
  en: {
    "app.startingLoom": "Starting Loom…",
    "app.newConversation": "New conversation",
    "app.serverDidNotStart": "Loom App Server did not start",
    "app.unknownConnectionError": "Unknown connection error",
    "app.retry": "Retry",

    "common.ready": "Ready",
    "common.working": "Working",
    "common.connecting": "Connecting",
    "common.archived": "Archived",
    "common.approval": "Approval",
    "common.defaultModel": "Default model",
    "common.localWorkspace": "Local workspace",
    "common.workspace": "Workspace",
    "common.copyWorkspacePath": "Copy workspace path: {path}",
    "common.permission": "Permission",
    "common.defaultAccess": "Default access",
    "common.openSettings": "Open Loom settings",
    "common.hideInspector": "Hide runtime inspector",
    "common.openInspector": "Open runtime inspector",

    "settings.back": "Back to Loom",
    "settings.title": "Settings",
    "settings.subtitle": "Local agent controls",
    "settings.search": "Search settings",
    "settings.runtime": "Loom runtime",
    "settings.turnActive": "Turn active",
    "settings.readyForChanges": "Ready for changes",

    "settings.group.loom": "Loom",
    "settings.group.integrations": "Integrations",
    "settings.group.advanced": "Advanced",
    "settings.nav.general": "General",
    "settings.nav.models": "Models",
    "settings.nav.capabilities": "Capabilities",
    "settings.nav.computer": "Computer Use",
    "settings.nav.browser": "Browser",
    "settings.nav.plugins": "Plugins",
    "settings.nav.skills": "Skills",
    "settings.nav.permissions": "Permissions",
    "settings.nav.developer": "Developer",

    "settings.status.preferenceOff": "Preference off",
    "settings.status.preferenceOn": "Preference on",
    "settings.status.runtimeMissing": "Preference on · runtime missing",
    "settings.status.ready": "Ready",
    "settings.status.runtimeNotReported": "Runtime status not reported",
    "settings.status.backendMissing": "Runtime backend missing",
    "settings.status.backendReady": "Runtime backend ready",
    "settings.status.on": "On",
    "settings.status.off": "Off",
    "settings.status.notReported": "Not reported",

    "settings.general.eyebrow": "Loom desktop",
    "settings.general.title": "General",
    "settings.general.description": "Manage the defaults and presentation of your local agent workspace from one place.",
    "settings.general.runtimeReady": "Runtime ready",
    "settings.general.loomWorking": "Loom is working",
    "settings.general.localReady": "Your local runtime is ready",
    "settings.general.overview": "One view of the model, permission boundary, exposed tools, and interface preferences currently shaping new agent work.",
    "settings.general.model": "Model",
    "settings.general.permission": "Permission",
    "settings.general.toolsExposed": "Tools exposed",
    "settings.general.capabilities": "Capabilities",
    "settings.general.enabledCount": "{enabled} / {total} enabled · {attachments}",
    "settings.general.runtimeDefaults": "Runtime defaults",
    "settings.general.runtimeDefaultsCaption": "The values Loom starts with when you create a new conversation.",
    "settings.general.defaultWorkspace": "Default workspace",
    "settings.general.defaultWorkspaceDesc": "New conversations begin here when no project is selected.",
    "settings.general.copyPath": "Copy path",
    "settings.general.defaultPermission": "Default permission",
    "settings.general.defaultPermissionDesc": "Review the execution boundary used for sensitive actions.",
    "settings.general.currentModel": "Current model",
    "settings.general.currentModelDesc": "Open model profiles and inspect the active inference route.",
    "settings.general.interface": "Interface",
    "settings.general.interfaceCaption": "Tune Loom for your screen without changing agent behavior.",
    "settings.general.theme": "Loom Dark",
    "settings.general.themeDesc": "Current desktop theme",
    "settings.general.systemTheme": "System theme",
    "settings.general.systemThemeDesc": "Follow the operating system",
    "settings.general.soon": "Soon",
    "settings.general.language": "Language",
    "settings.general.languageDesc": "Change the display language across the Loom desktop UI.",
    "settings.general.interfaceScale": "Interface scale",
    "settings.general.interfaceScaleDesc": "Scale the entire desktop surface for more comfortable reading.",
    "settings.general.reduceMotion": "Reduce motion",
    "settings.general.reduceMotionDesc": "Minimize decorative transitions, pulses, and animated status effects.",
    "settings.general.resetPresentation": "Reset presentation",
    "settings.general.resetPresentationDesc": "Return scale and motion preferences to the Loom defaults.",
    "settings.general.reset": "Reset",
    "settings.general.quickAccess": "Quick access",
    "settings.general.quickAccessCaption": "Jump straight to the settings that most often affect an agent run.",
    "settings.general.capabilitiesDesc": "Choose the tool families Loom can expose",
    "settings.general.modelsDesc": "Inspect active inference and saved profiles",
    "settings.general.diagnostics": "Runtime diagnostics",
    "settings.general.diagnosticsDesc": "Inspect tools, attachments, and integrations",
    "settings.toast.languageChanged": "Language changed to {language}.",
    "settings.toast.interfaceReset": "Interface preferences reset.",
    "settings.toast.workspaceCopied": "Workspace path copied.",
    "settings.toast.workspaceCopyFailed": "Could not copy the workspace path.",

    "settings.capabilities.eyebrow": "Agent runtime",
    "settings.capabilities.title": "Capabilities",
    "settings.capabilities.description": "Choose which major tool families Loom should expose. Preference switches and backend readiness are shown separately so disabled dependencies are not confused with a closed switch.",
    "settings.capabilities.toolsExposed": "{exposed} / {registered} tools exposed",
    "settings.capabilities.activeWarningTitle": "Finish or stop the active turn first.",
    "settings.capabilities.activeWarningBody": "Capability switches are locked while an agent turn is running so the tool set cannot change mid-execution.",
    "settings.capabilities.sectionTitle": "Agent capabilities",
    "settings.capabilities.sectionCaption": "The switch is the user's preference. The status badge reports whether the runtime is known to be ready.",

    "settings.capability.computer.title": "Computer Use",
    "settings.capability.computer.description": "Allow Loom to expose desktop control tools for screenshots, UIA, mouse, and keyboard actions.",
    "settings.capability.browser.title": "Browser Use",
    "settings.capability.browser.description": "Allow Loom to expose browser automation tools for Chrome/Edge sessions and local CDP attachment.",
    "settings.capability.webSearch.title": "Web Search",
    "settings.capability.webSearch.description": "Expose the configured public-web search provider to the agent.",
    "settings.capability.mcp.title": "MCP tools",
    "settings.capability.mcp.description": "Expose tools discovered from configured Model Context Protocol servers.",
    "settings.capability.skills.title": "Skills",
    "settings.capability.skills.description": "Let Loom discover and load reusable SKILL.md workflows for the active workspace.",
    "settings.capability.toolSearch.title": "Tool Search",
    "settings.capability.toolSearch.description": "Let the agent discover deferred integration tools on demand instead of loading everything up front.",
    "settings.capability.codeMode.title": "Code Mode",
    "settings.capability.codeMode.description": "Allow bounded multi-tool composition inside Loom's restricted execution language.",

    "settings.computer.eyebrow": "Desktop integration",
    "settings.computer.title": "Computer Use",
    "settings.computer.description": "Screenshot-driven Windows control with UI Automation assistance and post-action verification.",
    "settings.computer.runtimeNotWiredTitle": "Runtime status is not wired yet.",
    "settings.computer.runtimeNotWiredBody": "The switch is enabled, but the App Server has not reported whether the Windows operator is actually ready.",
    "settings.computer.runtimeMissingTitle": "Computer Use runtime is missing.",
    "settings.computer.runtimeMissingBody": "The preference switch is on, but Loom still needs the backend dependency before this capability can run.",
    "settings.computer.runtimeStatus": "Runtime status",
    "settings.computer.preferenceDesc": "Controls whether Loom should expose Computer Use tools.",
    "settings.computer.backendStatusDesc": "This must be ready before Loom can actually click, type, or observe desktop apps.",
    "settings.computer.operator": "Operator",
    "settings.computer.operatorDesc": "Backend responsible for screenshot capture, UIA discovery, and physical input.",
    "settings.computer.grounder": "Grounder",
    "settings.computer.grounderDesc": "Visual policy used for screenshot-based target selection.",
    "settings.computer.policyStep": "Policy step",
    "settings.computer.policyStepDesc": "Whether computer_step can plan and execute one grounded GUI action.",
    "settings.computer.observation": "Observation",
    "settings.computer.verification": "Verification",
    "settings.computer.safetyBoundary": "Safety boundary",
    "settings.computer.safetyCaption": "Computer Use remains subject to the conversation's permission profile even when the master switch is on.",
    "settings.computer.safetyTitle": "Sensitive GUI actions still cross Loom permissions.",
    "settings.computer.safetyBody": "Turning Computer Use on only allows exposure; it does not bypass Approval, Workspace, or Full Access policy.",

    "settings.browser.eyebrow": "Web interaction",
    "settings.browser.title": "Browser",
    "settings.browser.description": "Control Loom's browser-use session or attach to a local Chrome/Edge instance through loopback-only CDP.",
    "settings.browser.runtimeNotWiredBody": "The switch is enabled, but the App Server has not reported whether the browser backend is actually ready.",
    "settings.browser.runtimeMissingTitle": "Browser Use runtime is missing.",
    "settings.browser.runtimeMissingBody": "The preference switch is on, but Loom still needs the backend dependency before this capability can run.",
    "settings.browser.runtime": "Browser runtime",
    "settings.browser.preferenceDesc": "Controls whether Loom should expose Browser Use tools.",
    "settings.browser.backendStatusDesc": "This must be ready before Loom can open, attach, or operate browser pages.",
    "settings.browser.backend": "Backend",
    "settings.browser.connection": "Connection",
    "settings.browser.connectionDesc": "local-launch starts Loom's browser; cdp-attach controls an existing local Chrome/Edge process.",
    "settings.browser.externalBrowser": "External browser",
    "settings.browser.sessionPersistence": "Session persistence",
    "settings.browser.activeSessions": "Active sessions",

    "settings.detail.preference": "Preference",
    "settings.detail.backendStatus": "Backend status",
    "settings.detail.enabled": "Enabled",
    "settings.detail.unavailable": "Unavailable",

    "settings.models.eyebrow": "Inference",
    "settings.models.title": "Models",
    "settings.models.description": "Inspect the active model and saved profiles. Fast per-task switching can stay in the composer; full configuration belongs here.",
    "settings.models.activeModel": "Active model",
    "settings.models.current": "Current",
    "settings.models.savedProfiles": "Saved profiles",
    "settings.models.savedProfilesCaption": "Profiles configured in Loom's model manager.",
    "settings.models.noProfiles": "No saved model profiles.",

    "settings.plugins.eyebrow": "Extensions",
    "settings.plugins.title": "Plugins",
    "settings.plugins.description": "Installed Loom extensions belong here, separate from built-in runtime capabilities.",
    "settings.plugins.installed": "Installed plugins",
    "settings.plugins.caption": "Plugin activation may require a runtime restart.",
    "settings.plugins.loading": "Loading plugins…",
    "settings.plugins.none": "No plugins are installed.",
    "settings.plugins.plugin": "Plugin",
    "settings.plugins.installedExtension": "Installed extension",
    "settings.plugins.disabled": "Disabled",

    "settings.skills.eyebrow": "Reusable workflows",
    "settings.skills.title": "Skills",
    "settings.skills.description": "Codex-compatible SKILL.md workflows discovered from Loom and user skill roots.",
    "settings.skills.discovery": "Discovery",
    "settings.skills.discovered": "Discovered skills",
    "settings.skills.health": "Discovery health",
    "settings.skills.issues": "{count} issue(s)",

    "settings.permissions.eyebrow": "Execution safety",
    "settings.permissions.title": "Permissions",
    "settings.permissions.description": "Permission profiles determine whether sensitive file, process, browser, and GUI actions run automatically or require approval.",
    "settings.permissions.profiles": "Permission profiles",
    "settings.permissions.default": "Default",
    "settings.permissions.fullAccessDesc": "Broad execution authority for trusted local work.",
    "settings.permissions.workspaceDesc": "Prefer actions constrained to the active workspace.",
    "settings.permissions.approvalDesc": "Ask before sensitive actions.",

    "settings.developer.eyebrow": "Diagnostics",
    "settings.developer.title": "Developer",
    "settings.developer.description": "Runtime details useful when debugging Loom integrations and tool exposure.",
    "settings.developer.runtimeDiagnostics": "Runtime diagnostics",
    "settings.developer.registeredTools": "Registered tools",
    "settings.developer.exposedTools": "Exposed tools",
    "settings.developer.imageAttachments": "Image attachments",
    "settings.developer.fileAttachments": "File attachments",
    "settings.developer.capabilitySchema": "Capability schema",
    "settings.developer.integrationSummary": "Integration summary",
    "settings.developer.mcpServers": "MCP servers",
    "settings.developer.mcpTools": "MCP tools",
  },
  "zh-CN": {
    "app.startingLoom": "正在启动 Loom…",
    "app.newConversation": "新对话",
    "app.serverDidNotStart": "Loom 本地服务未启动",
    "app.unknownConnectionError": "未知连接错误",
    "app.retry": "重试",

    "common.ready": "就绪",
    "common.working": "工作中",
    "common.connecting": "连接中",
    "common.archived": "已归档",
    "common.approval": "待批准",
    "common.defaultModel": "默认模型",
    "common.localWorkspace": "本地工作区",
    "common.workspace": "工作区",
    "common.copyWorkspacePath": "复制工作区路径：{path}",
    "common.permission": "权限",
    "common.defaultAccess": "默认权限",
    "common.openSettings": "打开 Loom 设置",
    "common.hideInspector": "隐藏运行检查器",
    "common.openInspector": "打开运行检查器",

    "settings.back": "返回应用",
    "settings.title": "设置",
    "settings.subtitle": "本地 Agent 控制中心",
    "settings.search": "搜索设置",
    "settings.runtime": "Loom 运行时",
    "settings.turnActive": "任务运行中",
    "settings.readyForChanges": "可以修改设置",

    "settings.group.loom": "Loom",
    "settings.group.integrations": "集成",
    "settings.group.advanced": "高级",
    "settings.nav.general": "常规",
    "settings.nav.models": "模型",
    "settings.nav.capabilities": "能力",
    "settings.nav.computer": "电脑操控",
    "settings.nav.browser": "浏览器",
    "settings.nav.plugins": "插件",
    "settings.nav.skills": "技能",
    "settings.nav.permissions": "权限",
    "settings.nav.developer": "开发者",

    "settings.status.preferenceOff": "偏好已关闭",
    "settings.status.preferenceOn": "偏好已开启",
    "settings.status.runtimeMissing": "偏好开启 · 运行时缺失",
    "settings.status.ready": "可用",
    "settings.status.runtimeNotReported": "运行时状态未上报",
    "settings.status.backendMissing": "运行后端缺失",
    "settings.status.backendReady": "运行后端可用",
    "settings.status.on": "开启",
    "settings.status.off": "关闭",
    "settings.status.notReported": "未上报",

    "settings.general.eyebrow": "Loom 桌面端",
    "settings.general.title": "常规",
    "settings.general.description": "统一管理本地 Agent 工作区的默认行为和界面显示。",
    "settings.general.runtimeReady": "运行时就绪",
    "settings.general.loomWorking": "Loom 正在工作",
    "settings.general.localReady": "本地运行时已就绪",
    "settings.general.overview": "集中查看当前影响新任务的模型、权限边界、暴露工具和界面偏好。",
    "settings.general.model": "模型",
    "settings.general.permission": "权限",
    "settings.general.toolsExposed": "暴露工具",
    "settings.general.capabilities": "能力",
    "settings.general.enabledCount": "已开启 {enabled} / {total} · 附件 {attachments}",
    "settings.general.runtimeDefaults": "运行默认值",
    "settings.general.runtimeDefaultsCaption": "新建对话时 Loom 默认采用的配置。",
    "settings.general.defaultWorkspace": "默认工作区",
    "settings.general.defaultWorkspaceDesc": "未选择项目时，新对话会从这里开始。",
    "settings.general.copyPath": "复制路径",
    "settings.general.defaultPermission": "默认权限",
    "settings.general.defaultPermissionDesc": "查看敏感操作采用的执行边界。",
    "settings.general.currentModel": "当前模型",
    "settings.general.currentModelDesc": "打开模型配置，检查当前推理线路。",
    "settings.general.interface": "界面",
    "settings.general.interfaceCaption": "调整显示体验，不改变 Agent 行为。",
    "settings.general.theme": "Loom 深色",
    "settings.general.themeDesc": "当前桌面主题",
    "settings.general.systemTheme": "跟随系统",
    "settings.general.systemThemeDesc": "跟随操作系统外观",
    "settings.general.soon": "稍后支持",
    "settings.general.language": "语言",
    "settings.general.languageDesc": "切换整个 Loom 桌面端的显示语言。",
    "settings.general.interfaceScale": "界面缩放",
    "settings.general.interfaceScaleDesc": "缩放整个桌面界面，方便阅读。",
    "settings.general.reduceMotion": "减少动画",
    "settings.general.reduceMotionDesc": "减少装饰性过渡、脉冲和状态动画。",
    "settings.general.resetPresentation": "重置显示",
    "settings.general.resetPresentationDesc": "将缩放和动画偏好恢复为默认值。",
    "settings.general.reset": "重置",
    "settings.general.quickAccess": "快捷入口",
    "settings.general.quickAccessCaption": "快速进入最常影响 Agent 运行的设置。",
    "settings.general.capabilitiesDesc": "选择 Loom 可以暴露的工具能力",
    "settings.general.modelsDesc": "查看当前推理和已保存模型配置",
    "settings.general.diagnostics": "运行诊断",
    "settings.general.diagnosticsDesc": "检查工具、附件和集成状态",
    "settings.toast.languageChanged": "语言已切换为 {language}。",
    "settings.toast.interfaceReset": "界面显示偏好已重置。",
    "settings.toast.workspaceCopied": "工作区路径已复制。",
    "settings.toast.workspaceCopyFailed": "无法复制工作区路径。",

    "settings.capabilities.eyebrow": "Agent 运行时",
    "settings.capabilities.title": "能力",
    "settings.capabilities.description": "选择 Loom 要向模型暴露哪些主要工具能力。偏好开关和后端就绪状态会分开显示，避免把依赖缺失误认为开关关闭。",
    "settings.capabilities.toolsExposed": "已暴露 {exposed} / {registered} 个工具",
    "settings.capabilities.activeWarningTitle": "请先结束或停止当前任务。",
    "settings.capabilities.activeWarningBody": "Agent 正在运行时会锁定能力开关，避免执行中途改变工具集合。",
    "settings.capabilities.sectionTitle": "Agent 能力",
    "settings.capabilities.sectionCaption": "开关代表用户偏好，状态标记代表运行时是否真的可用。",

    "settings.capability.computer.title": "电脑操控",
    "settings.capability.computer.description": "允许 Loom 暴露截图、UIA、鼠标和键盘等桌面控制工具。",
    "settings.capability.browser.title": "浏览器控制",
    "settings.capability.browser.description": "允许 Loom 暴露 Chrome/Edge 会话和本地 CDP 连接相关的浏览器自动化工具。",
    "settings.capability.webSearch.title": "网页搜索",
    "settings.capability.webSearch.description": "向 Agent 暴露已配置的公网搜索提供商。",
    "settings.capability.mcp.title": "MCP 工具",
    "settings.capability.mcp.description": "暴露从已配置 Model Context Protocol 服务发现到的工具。",
    "settings.capability.skills.title": "技能",
    "settings.capability.skills.description": "让 Loom 发现并加载当前工作区可用的 SKILL.md 工作流。",
    "settings.capability.toolSearch.title": "工具搜索",
    "settings.capability.toolSearch.description": "让 Agent 按需发现延迟加载的集成工具，而不是一开始全部塞进上下文。",
    "settings.capability.codeMode.title": "代码模式",
    "settings.capability.codeMode.description": "允许在 Loom 受限执行语言中组合多个工具调用。",

    "settings.computer.eyebrow": "桌面集成",
    "settings.computer.title": "电脑操控",
    "settings.computer.description": "基于截图驱动的 Windows 控制，辅助 UI Automation 识别并在操作后校验。",
    "settings.computer.runtimeNotWiredTitle": "运行时状态还没有接上。",
    "settings.computer.runtimeNotWiredBody": "开关已经开启，但 App Server 还没有上报 Windows 操作后端是否真正可用。",
    "settings.computer.runtimeMissingTitle": "电脑操控运行时缺失。",
    "settings.computer.runtimeMissingBody": "偏好开关已开启，但 Loom 还需要对应后端依赖后才能执行这个能力。",
    "settings.computer.runtimeStatus": "运行状态",
    "settings.computer.preferenceDesc": "控制 Loom 是否向模型暴露电脑操控工具。",
    "settings.computer.backendStatusDesc": "必须可用后，Loom 才能真正点击、输入或观察桌面应用。",
    "settings.computer.operator": "操作后端",
    "settings.computer.operatorDesc": "负责截图、UIA 控件发现和真实输入的后端。",
    "settings.computer.grounder": "视觉定位器",
    "settings.computer.grounderDesc": "用于基于截图选择目标位置的视觉策略。",
    "settings.computer.policyStep": "策略执行步",
    "settings.computer.policyStepDesc": "表示 computer_step 是否能规划并执行一次 GUI 动作。",
    "settings.computer.observation": "观察模式",
    "settings.computer.verification": "操作校验",
    "settings.computer.safetyBoundary": "安全边界",
    "settings.computer.safetyCaption": "即使总开关开启，电脑操控仍然受当前对话权限配置约束。",
    "settings.computer.safetyTitle": "敏感 GUI 操作仍会经过 Loom 权限系统。",
    "settings.computer.safetyBody": "开启电脑操控只代表允许暴露工具，不会绕过 Approval、Workspace 或 Full Access 策略。",

    "settings.browser.eyebrow": "网页交互",
    "settings.browser.title": "浏览器",
    "settings.browser.description": "控制 Loom 的 browser-use 会话，或通过只允许本地回环的 CDP 连接接管本机 Chrome/Edge。",
    "settings.browser.runtimeNotWiredBody": "开关已经开启，但 App Server 还没有上报浏览器后端是否真正可用。",
    "settings.browser.runtimeMissingTitle": "浏览器运行时缺失。",
    "settings.browser.runtimeMissingBody": "偏好开关已开启，但 Loom 还需要对应后端依赖后才能执行这个能力。",
    "settings.browser.runtime": "浏览器运行时",
    "settings.browser.preferenceDesc": "控制 Loom 是否向模型暴露浏览器控制工具。",
    "settings.browser.backendStatusDesc": "必须可用后，Loom 才能打开、连接或操作浏览器页面。",
    "settings.browser.backend": "后端",
    "settings.browser.connection": "连接方式",
    "settings.browser.connectionDesc": "local-launch 启动 Loom 自己的浏览器；cdp-attach 接管已有的本机 Chrome/Edge 进程。",
    "settings.browser.externalBrowser": "外部浏览器",
    "settings.browser.sessionPersistence": "会话持久化",
    "settings.browser.activeSessions": "活动会话",

    "settings.detail.preference": "偏好",
    "settings.detail.backendStatus": "后端状态",
    "settings.detail.enabled": "已启用",
    "settings.detail.unavailable": "不可用",

    "settings.models.eyebrow": "推理",
    "settings.models.title": "模型",
    "settings.models.description": "查看当前模型和已保存配置。输入框里保留快速切换，完整配置统一放在这里。",
    "settings.models.activeModel": "当前模型",
    "settings.models.current": "当前",
    "settings.models.savedProfiles": "已保存配置",
    "settings.models.savedProfilesCaption": "Loom 模型管理器中配置的模型档案。",
    "settings.models.noProfiles": "暂无已保存模型配置。",

    "settings.plugins.eyebrow": "扩展",
    "settings.plugins.title": "插件",
    "settings.plugins.description": "已安装的 Loom 扩展放在这里，和内置运行能力分开。",
    "settings.plugins.installed": "已安装插件",
    "settings.plugins.caption": "插件启用状态可能需要重启运行时后生效。",
    "settings.plugins.loading": "正在加载插件…",
    "settings.plugins.none": "暂无已安装插件。",
    "settings.plugins.plugin": "插件",
    "settings.plugins.installedExtension": "已安装扩展",
    "settings.plugins.disabled": "已禁用",

    "settings.skills.eyebrow": "可复用工作流",
    "settings.skills.title": "技能",
    "settings.skills.description": "从 Loom 和用户技能目录发现 Codex 兼容的 SKILL.md 工作流。",
    "settings.skills.discovery": "发现状态",
    "settings.skills.discovered": "已发现技能",
    "settings.skills.health": "发现健康度",
    "settings.skills.issues": "{count} 个问题",

    "settings.permissions.eyebrow": "执行安全",
    "settings.permissions.title": "权限",
    "settings.permissions.description": "权限配置决定文件、进程、浏览器和 GUI 敏感操作是自动执行还是需要批准。",
    "settings.permissions.profiles": "权限配置",
    "settings.permissions.default": "默认",
    "settings.permissions.fullAccessDesc": "适合可信本地工作的宽权限执行模式。",
    "settings.permissions.workspaceDesc": "优先把操作限制在当前工作区内。",
    "settings.permissions.approvalDesc": "敏感操作执行前先询问。",

    "settings.developer.eyebrow": "诊断",
    "settings.developer.title": "开发者",
    "settings.developer.description": "调试 Loom 集成和工具暴露时使用的运行时详情。",
    "settings.developer.runtimeDiagnostics": "运行诊断",
    "settings.developer.registeredTools": "注册工具",
    "settings.developer.exposedTools": "暴露工具",
    "settings.developer.imageAttachments": "图片附件",
    "settings.developer.fileAttachments": "文件附件",
    "settings.developer.capabilitySchema": "能力配置版本",
    "settings.developer.integrationSummary": "集成摘要",
    "settings.developer.mcpServers": "MCP 服务",
    "settings.developer.mcpTools": "MCP 工具",
  },
};

const I18nContext = createContext<I18nContextValue | null>(null);

function normalizeLanguage(value: unknown): LoomLanguage | null {
  const raw = String(value ?? "").trim();
  if (raw === "zh-CN" || raw === "zh" || raw.toLowerCase().startsWith("zh")) return "zh-CN";
  if (raw === "en" || raw.toLowerCase().startsWith("en")) return "en";
  return null;
}

function detectInitialLanguage(): LoomLanguage {
  try {
    const stored = normalizeLanguage(window.localStorage.getItem(LANGUAGE_STORAGE_KEY));
    if (stored) return stored;
  } catch {
    // Ignore storage failures and fall back to the browser locale.
  }
  const candidates = [navigator.language, ...(navigator.languages ?? [])];
  for (const item of candidates) {
    const normalized = normalizeLanguage(item);
    if (normalized) return normalized;
  }
  return "en";
}

function persistLanguage(language: LoomLanguage): void {
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    // The in-memory setting still applies to this renderer session.
  }
}

function applyDocumentLanguage(language: LoomLanguage): void {
  document.documentElement.lang = language;
  document.documentElement.dataset.loomLanguage = language;
}

function interpolate(template: string, values?: TranslationValues): string {
  if (!values) return template;
  return template.replace(/\{(\w+)\}/g, (_, key: string) => String(values[key] ?? `{${key}}`));
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<LoomLanguage>(() => detectInitialLanguage());

  useEffect(() => {
    applyDocumentLanguage(language);
    persistLanguage(language);
  }, [language]);

  const value = useMemo<I18nContextValue>(() => ({
    language,
    setLanguage: (next) => setLanguageState(next),
    t: (key, values) => {
      const table = TRANSLATIONS[language] ?? TRANSLATIONS.en;
      return interpolate(table[key] ?? TRANSLATIONS.en[key] ?? key, values);
    },
  }), [language]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside I18nProvider");
  return value;
}

export function currentLanguageLabel(language: LoomLanguage): string {
  return LOOM_LANGUAGES.find((item) => item.value === language)?.nativeLabel ?? language;
}

export function bootstrapDocumentLanguage(): void {
  applyDocumentLanguage(detectInitialLanguage());
}
