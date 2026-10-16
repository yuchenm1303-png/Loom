export type SoftwareUpdatePhase =
  | "disabled"
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "downloaded"
  | "up-to-date"
  | "error";

export interface SoftwareUpdateState {
  hostRuntime?: { enabled: boolean; phase: string; currentVersion: string; availableVersion?: string; percent?: number; error?: string; requiredBootstrapVersion?: string };
  enabled: boolean;
  accountUpdatesAllowed?: boolean;
  automaticUpdates?: boolean;
  silentUpdates?: boolean;
  autoRestartAfterUpdate?: boolean;
  phase: SoftwareUpdatePhase;
  currentVersion: string;
  availableVersion?: string;
  releaseName?: string | null;
  releaseDate?: string;
  percent?: number;
  transferred?: number;
  total?: number;
  bytesPerSecond?: number;
  checkedAt?: string;
  error?: string;
}

export function statusCopy(state: SoftwareUpdateState | null): {
  title: string;
  detail: string;
  tone: "neutral" | "active" | "success" | "warning" | "error";
} {
  if (!state) return { title: "Reading update status", detail: "Connecting to the desktop updater…", tone: "neutral" };
  const host = state.hostRuntime;
  if (host?.enabled && ["idle", "up-to-date"].includes(state.phase) && ["checking", "available", "downloading", "ready", "activating", "incompatible", "error"].includes(host.phase)) {
    return { title: "Loom Host 更新", detail: host.error || (host.phase === "downloading" ? `正在下载后台运行组件：${host.percent ?? 0}%` : host.phase === "ready" ? "运行组件已下载，等待任务空闲后自动启用。" : host.phase === "activating" ? "正在启用新的运行组件…" : host.phase === "incompatible" ? `需要先更新桌面版至 ${host.requiredBootstrapVersion || "新版本"}。` : "正在检查后台运行组件更新…"), tone: host.error ? "error" : "active" };
  }
  switch (state.phase) {
    case "disabled":
      if (state.accountUpdatesAllowed === false) return {
        title: "Update access could not be verified",
        detail: "Check your sign-in, connection, and account update permission, then try checking again.",
        tone: "warning",
      };
      return {
        title: "Updates are unavailable in this build",
        detail: "Automatic updates are enabled in installed Windows builds, not development or unpackaged sessions.",
        tone: "neutral",
      };
    case "checking":
      return { title: "Checking for updates", detail: "Looking for the newest Loom Windows release…", tone: "active" };
    case "available":
      return {
        title: `Loom ${state.availableVersion || "update"} is available`,
        detail: "The update was found and the download will begin automatically.",
        tone: "active",
      };
    case "downloading":
      return {
        title: `Downloading Loom ${state.availableVersion || "update"}`,
        detail: "You can keep working while Loom downloads the update in the background.",
        tone: "active",
      };
    case "downloaded":
      return {
        title: `Loom ${state.availableVersion || "update"} is ready`,
        detail: "Restart when convenient to finish installing the downloaded update.",
        tone: "success",
      };
    case "up-to-date":
      return { title: "Loom is up to date", detail: `You are running the newest available version, ${state.currentVersion}.`, tone: "success" };
    case "error":
      return {
        title: "Loom couldn't check for updates",
        detail: state.error || "The update service returned an unexpected error. Try again in a moment.",
        tone: "error",
      };
    default:
      if (state.automaticUpdates === false) return { title: "Automatic updates are off", detail: "You can check for updates manually.", tone: "neutral" };
      return { title: "Automatic updates are on", detail: "Loom checks in the background and downloads newer releases automatically.", tone: "neutral" };
  }
}
