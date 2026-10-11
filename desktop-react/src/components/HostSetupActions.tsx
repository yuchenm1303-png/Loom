import { useEffect, useState } from "react";
import { ArrowRight, Download } from "./icons";
import { discoverLocalLoomHost } from "../localHostDiscovery";
import "./host-setup-actions.css";

export function HostSetupActions({ zh, download, onRetry, hostDetected = false }: {
  zh: boolean; download: string; onRetry: () => void; hostDetected?: boolean;
}) {
  const [launchState, setLaunchState] = useState<"idle" | "waiting" | "timeout">("idle");
  const launching = launchState === "waiting";
  const [checking, setChecking] = useState(false);
  const checkHost = async () => {
    if (checking) return;
    setChecking(true);
    try {
      const host = await discoverLocalLoomHost();
      if (host) onRetry();
      else setLaunchState("timeout");
    } finally { setChecking(false); }
  };
  useEffect(() => {
    if (!launching) return;
    const timer = window.setTimeout(() => setLaunchState("timeout"), 20000);
    return () => window.clearTimeout(timer);
  }, [launching]);

  return <div className="loom-host-setup">
    {hostDetected ? <>
      <p>{zh ? "已找到本机 Host。请检查网络连接，或重新关联后重试。" : "Host is running on this computer. Check your connection or retry pairing."}</p>
      <button className="loom-form-submit" type="button" onClick={onRetry}>{zh ? "重新连接" : "Reconnect"}<ArrowRight size={16} aria-hidden="true" /></button>
    </> : <>
      <p className="loom-host-setup-title">{zh ? "已经安装 Loom？" : "Already installed Loom?"}</p>
      <a className="loom-form-submit" href="loom://host/start" onClick={() => { setLaunchState("waiting"); window.dispatchEvent(new Event("loom:web-host-launch")); }}>
        <span>{launching ? (zh ? "已请求启动，等待检测…" : "Launch requested · waiting…") : launchState === "timeout" ? (zh ? "再次尝试打开 Loom" : "Try opening Loom again") : (zh ? "启动 Loom Host" : "Start Loom Host")}</span><ArrowRight size={16} aria-hidden="true" />
      </a>
      <p className="loom-host-setup-note" role="status" aria-live="polite">{launchState === "timeout"
        ? (zh ? "仍无法访问本机 Host，启动尚未确认。请先从开始菜单打开 Loom，再点检测。" : "Cannot reach local Host; launch is not confirmed. Open Loom from the Start menu, then detect it below.")
        : (zh ? "在浏览器提示中允许打开 Loom；检测到 Host 后才会连接。" : "Allow the browser to open Loom when prompted. Connection starts once Host is detected.")}</p>
      <button className="loom-host-setup-retry" type="button" disabled={checking} onClick={() => void checkHost()}>{checking ? (zh ? "正在检测…" : "Detecting…") : (zh ? "已打开 Loom？检测并连接" : "Opened Loom? Detect and connect")}</button>
      <details className="loom-host-launch-guide">
        <summary>{zh ? "没有启动或检测不到？" : "No launch or Host still missing?"}</summary>
        <ol>
          <li>{zh ? "点击启动后，在浏览器提示中允许打开 Loom。没有提示或没有响应？从 Windows 开始菜单打开 Loom。" : "After clicking Start, allow your browser to open Loom. No prompt or response? Open Loom from the Windows Start menu."}</li>
          <li>{zh ? "如果浏览器询问本地网络访问权限，请允许本网站访问这台电脑上的应用。" : "If your browser asks for local network access, allow this site to reach apps on this computer."}</li>
          <li>{zh ? "留在此页，Host 启动后会自动检测并关联。连接成功后，点击进入工作区。" : "Stay on this page while Host starts. It will be detected and paired automatically; then click Open workspace."}</li>
        </ol>
        {launchState === "timeout" ? <p className="loom-host-setup-note">{zh ? "开始菜单也无法启动？安装下方最新版以修复启动支持，再点重新检测。" : "Cannot start Loom from the Start menu either? Install the latest version below to repair launch support, then Check again."}</p> : null}
      </details>
      <div className="loom-host-setup-install">
        <p className="loom-host-setup-title">{zh ? "第一次在这台电脑使用？" : "First time on this computer?"}</p>
        <p className="loom-host-setup-note">{zh ? "安装一次即可。Host 包含在内，桌面窗口无需保持打开。" : "Install once. Host is included; the desktop window can stay closed."}</p>
        <a className="loom-secondary-action" href={download}><span>{zh ? "安装 Loom · 包含 Host" : "Install Loom · Host included"}</span><Download size={16} aria-hidden="true" /></a>
      </div>
      <button className="loom-host-setup-retry" type="button" onClick={onRetry}>{zh ? "重新检测" : "Check again"}</button>
    </>}
  </div>;
}
