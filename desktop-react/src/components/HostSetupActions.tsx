import { useEffect, useState } from "react";
import { ArrowRight, Download } from "lucide-react";
import "./host-setup-actions.css";

export function HostSetupActions({ zh, download, onRetry, hostDetected = false }: {
  zh: boolean; download: string; onRetry: () => void; hostDetected?: boolean;
}) {
  const [launching, setLaunching] = useState(false);
  useEffect(() => {
    if (!launching) return;
    const timer = window.setTimeout(() => setLaunching(false), 12000);
    return () => window.clearTimeout(timer);
  }, [launching]);

  return <div className="loom-host-setup">
    {hostDetected ? <>
      <p>{zh ? "已找到本机 Host。请检查网络连接，或重新关联后重试。" : "Host is running on this computer. Check your connection or retry pairing."}</p>
      <button className="loom-form-submit" type="button" onClick={onRetry}>{zh ? "重新连接" : "Reconnect"}<ArrowRight size={16} aria-hidden="true" /></button>
    </> : <>
      <p className="loom-host-setup-title">{zh ? "已经安装 Loom？" : "Already installed Loom?"}</p>
      <a className="loom-form-submit" href="loom://host/start" onClick={() => { setLaunching(true); onRetry(); }}>
        <span>{launching ? (zh ? "等待 Host 启动…" : "Waiting for Host…") : (zh ? "启动 Loom Host" : "Start Loom Host")}</span><ArrowRight size={16} aria-hidden="true" />
      </a>
      <p className="loom-host-setup-note">{zh ? "允许浏览器启动 Loom 后，本页会自动连接。桌面窗口无需保持打开。" : "Allow your browser to start Loom; this page will connect automatically. You can keep the desktop window closed."}</p>
      {launching ? <p role="status" className="loom-host-setup-note">{zh ? "如果没有响应，可从 Windows 开始菜单启动 Loom，随后关闭桌面窗口。" : "If nothing happens, start Loom from the Windows Start menu, then close its desktop window."}</p> : null}
      <div className="loom-host-setup-install">
        <p className="loom-host-setup-title">{zh ? "第一次在这台电脑使用？" : "First time on this computer?"}</p>
        <p className="loom-host-setup-note">{zh ? "安装一次 Loom，即包含本机 Host 和 Agent。完成关联后，以后打开网页即可使用；新版 Host 会随 Windows 登录自动启动。" : "Install Loom once for the local Host and Agent. After pairing, just open the web. The new Host starts automatically when you sign in to Windows."}</p>
        <a className="loom-secondary-action" href={download}><span>{zh ? "安装 Loom · 包含 Host" : "Install Loom · Host included"}</span><Download size={16} aria-hidden="true" /></a>
      </div>
      <button className="loom-host-setup-retry" type="button" onClick={onRetry}>{zh ? "重新检测" : "Check again"}</button>
    </>}
  </div>;
}
