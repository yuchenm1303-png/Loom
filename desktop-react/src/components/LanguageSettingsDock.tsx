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
  "Refresh": "刷新",
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
  "Not reported": "未上报",
  "Backend missing": "后端缺失",
  "Ready": "可用",
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
  "Verification": "操作校验",
  "Safety boundary": "安全边界",
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
