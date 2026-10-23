import { createRoot } from "react-dom/client";
import { PortalLiquidCursor } from "../../src/components/PortalLiquidCursor";
import "../../src/components/portal-liquid-cursor.css";

createRoot(document.getElementById("root")!).render(
  <main className="loom-site loom-portal-page" data-loom-site-theme="dark">
    <style>{`
      .loom-site { height:100vh; overflow:auto; background:#1f1e1d; color:#f0ede4; font:18px Georgia; }
      .loom-site[data-loom-site-theme=light] { background:#fbfaf7; color:#1b1a18; }
      .sheet { margin:80px; min-height:1400px; }
      button, input, a { font:inherit; color:inherit; background:transparent; border:1px solid currentColor; padding:12px 22px; }
      #first { margin-right:140px; }
      #wide { display:block; width:780px; margin-top:100px; }
      #tall { display:block; width:170px; height:160px; margin-top:40px; }
      #theme { position:relative; width:70px; height:34px; padding:0; border-radius:20px; margin-top:40px; }
      #theme::after { content:""; position:absolute; top:4px; left:4px; width:24px; height:24px; border-radius:50%; background:currentColor; transition:transform .2s; }
      #theme[aria-checked=true]::after { transform:translateX(36px); }
      p { width:380px; line-height:1.8; border-bottom:1px solid currentColor; padding-bottom:20px; }
    `}</style>
    <PortalLiquidCursor />
    <div style={{ position: "absolute", left: 780, top: 210, fontSize: 84, pointerEvents: "none" }}>Loom</div>
    <div className="sheet">
      <button id="first" onClick={event => { event.currentTarget.dataset.clicks = String(Number(event.currentTarget.dataset.clicks || 0) + 1); }}>Open Loom</button>
      <button id="second">Another target</button>
      <p>Inspect a project and make something useful.<br />这是多行中文，用来检查透镜里面的文字换行是否准确。页面内容应当清晰稳定。</p>
      <input id="wide" placeholder="Ask Loom to inspect, edit, run, browse…" />
      <button id="tall">A tall target</button>
      <button id="theme" role="switch" aria-checked="false" aria-label="Change theme" onClick={event => {
        const next = event.currentTarget.getAttribute("aria-checked") !== "true";
        event.currentTarget.setAttribute("aria-checked", String(next));
        event.currentTarget.closest("main")!.setAttribute("data-loom-site-theme", next ? "light" : "dark");
      }} />
      <a id="below" href="#first" style={{ display: "block", marginTop: 300 }}>Below the fold</a>
    </div>
  </main>,
);
