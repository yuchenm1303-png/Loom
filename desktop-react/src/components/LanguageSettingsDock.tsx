import { Globe2 } from "lucide-react";
import { useEffect } from "react";
import { LOOM_LANGUAGES, currentLanguageLabel, useI18n, type LoomLanguage } from "../i18n";
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
  "Models": "模型",
  "Capabilities": "能力",
  "Computer Use": "电脑操控",
  "Browser": "浏览器",
  "Plugins": "插件",
  "Skills": "技能",
  "Permissions": "权限",
  "Developer": "开发者",
  "Integrations": "集成",
  "Advanced": "高级",
  "Loom desktop": "Loom 桌面端",
  "Manage the defaults and presentation of your local agent workspace from one place.": "统一管理本地 Agent 工作区的默认行为和界面显示。",
  "Runtime ready": "运行时就绪",
  "Loom is working": "Loom 正在工作",
  "Your local runtime is ready": "本地运行时已就绪",
  "One view of the model, permission boundary, exposed tools, and interface preferences currently shaping new agent work.": "集中查看当前影响新任务的模型、权限边界、暴露工具和界面偏好。",
  "Model": "模型",
  "Permission": "权限",
  "Tools exposed": "暴露工具",
  "Runtime defaults": "运行默认值",
  "The values Loom starts with when you create a new conversation.": "新建对话时 Loom 默认采用的配置。",
  "Default workspace": "默认工作区",
  "New conversations begin here when no project is selected.": "未选择项目时，新对话会从这里开始。",
  "Copy path": "复制路径",
  "Default permission": "默认权限",
  "Review the execution boundary used for sensitive actions.": "查看敏感操作采用的执行边界。",
  "Current model": "当前模型",
  "Open model profiles and inspect the active inference route.": "打开模型配置，检查当前推理线路。",
  "Interface": "界面",
  "Tune Loom for your screen without changing agent behavior.": "调整显示体验，不改变 Agent 行为。",
  "Loom Dark": "Loom 深色",
  "Current desktop theme": "当前桌面主题",
  "System theme": "跟随系统",
  "Follow the operating system": "跟随操作系统外观",
  "Soon": "稍后支持",
  "Interface scale": "界面缩放",
  "Scale the entire desktop surface for more comfortable reading.": "缩放整个桌面界面，方便阅读。",
  "Reduce motion": "减少动画",
  "Minimize decorative transitions, pulses, and animated status effects.": "减少装饰性过渡、脉冲和状态动画。",
  "Reset presentation": "重置显示",
  "Return scale and motion preferences to the Loom defaults.": "将缩放和动画偏好恢复为默认值。",
  "Reset": "重置",
  "Quick access": "快捷入口",
  "Jump straight to the settings that most often affect an agent run.": "快速进入最常影响 Agent 运行的设置。",
  "Choose the tool families Loom can expose": "选择 Loom 可以暴露的工具能力",
  "Inspect active inference and saved profiles": "查看当前推理和已保存模型配置",
  "Runtime diagnostics": "运行诊断",
  "Inspect tools, attachments, and integrations": "检查工具、附件和集成状态",
  "Agent runtime": "Agent 运行时",
  "Choose which major tool families Loom should expose. Preference switches and backend readiness are shown separately so disabled dependencies are not confused with a closed switch.": "选择 Loom 要向模型暴露哪些主要工具能力。偏好开关和后端就绪状态会分开显示，避免把依赖缺失误认为开关关闭。",
  "Agent capabilities": "Agent 能力",
  "The switch is the user's preference. The status badge reports whether the runtime is known to be ready.": "开关代表用户偏好，状态标记代表运行时是否真的可用。",
  "Preference off": "偏好已关闭",
  "Preference on": "偏好已开启",
  "Preference on · runtime missing": "偏好开启 · 运行时缺失",
  "Ready": "可用",
  "Runtime status not reported": "运行时状态未上报",
  "Runtime backend missing": "运行后端缺失",
  "Runtime backend ready": "运行后端可用",
  "Desktop integration": "桌面集成",
  "Screenshot-driven Windows control with UI Automation assistance and post-action verification.": "基于截图驱动的 Windows 控制，辅助 UI Automation 识别并在操作后校验。",
  "Runtime status": "运行状态",
  "Preference": "偏好",
  "Backend status": "后端状态",
  "Operator": "操作后端",
  "Grounder": "视觉定位器",
  "Policy step": "策略执行步",
  "Observation": "观察模式",
  "Verification": "操作校验",
  "Safety boundary": "安全边界",
  "Web interaction": "网页交互",
  "Browser runtime": "浏览器运行时",
  "Backend": "后端",
  "Connection": "连接方式",
  "External browser": "外部浏览器",
  "Session persistence": "会话持久化",
  "Active sessions": "活动会话",
  "Inference": "推理",
  "Active model": "当前模型",
  "Current": "当前",
  "Saved profiles": "已保存配置",
  "Profiles configured in Loom's model manager.": "Loom 模型管理器中配置的模型档案。",
  "No saved model profiles.": "暂无已保存模型配置。",
  "Extensions": "扩展",
  "Installed plugins": "已安装插件",
  "Plugin activation may require a runtime restart.": "插件启用状态可能需要重启运行时后生效。",
  "Loading plugins…": "正在加载插件…",
  "No plugins are installed.": "暂无已安装插件。",
  "Reusable workflows": "可复用工作流",
  "Discovery": "发现状态",
  "Discovered skills": "已发现技能",
  "Discovery health": "发现健康度",
  "Execution safety": "执行安全",
  "Permission profiles": "权限配置",
  "Diagnostics": "诊断",
  "Registered tools": "注册工具",
  "Exposed tools": "暴露工具",
  "Image attachments": "图片附件",
  "File attachments": "文件附件",
  "Capability schema": "能力配置版本",
  "Integration summary": "集成摘要",
  "MCP servers": "MCP 服务",
  "MCP tools": "MCP 工具",
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

export function LanguageSettingsDock() {
  const { language, setLanguage, t } = useI18n();

  useEffect(() => {
    applyStaticSettingsLanguage(language);
    const root = document.querySelector(".settings-shell");
    if (!root) return;
    const observer = new MutationObserver(() => applyStaticSettingsLanguage(language));
    observer.observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["title", "aria-label", "placeholder"] });
    return () => observer.disconnect();
  }, [language]);

  const chooseLanguage = (next: LoomLanguage) => {
    if (next === language) return;
    setLanguage(next);
    window.dispatchEvent(new CustomEvent("loom:language-updated", {
      detail: { language: next },
    }));
  };

  return (
    <section className="language-settings-dock" aria-label={t("settings.general.language")}>
      <div className="language-settings-dock-copy">
        <span className="language-settings-dock-icon" aria-hidden="true">
          <Globe2 size={16} strokeWidth={1.8} />
        </span>
        <div>
          <strong>{t("settings.general.language")}</strong>
          <span>{t("settings.general.languageDesc")}</span>
        </div>
      </div>

      <div className="language-settings-options" role="group" aria-label={t("settings.general.language")}>
        {LOOM_LANGUAGES.map((item) => (
          <button
            type="button"
            key={item.value}
            className={item.value === language ? "active" : ""}
            onClick={() => chooseLanguage(item.value)}
            aria-pressed={item.value === language}
            title={item.label}
          >
            {item.nativeLabel}
          </button>
        ))}
      </div>

      <span className="language-settings-current">{currentLanguageLabel(language)}</span>
    </section>
  );
}
