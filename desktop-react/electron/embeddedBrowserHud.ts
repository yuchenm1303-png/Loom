import type { WebContents } from "electron";

const WORLD = 991;
const HUD = "globalThis.__loomBrowserHudRuntimeV2";
const HOST = "document.getElementById('loom-browser-hud-root-v2')";
const LABELS: Record<string, string> = {
  state: "正在读取页面", tabs: "正在读取标签页", cookies: "正在读取网站数据", clear_cookies: "正在清理网站数据",
  navigate: "正在打开页面", click: "正在点击", click_at: "正在点击", hover: "正在定位元素",
  type_text: "正在输入", send_text: "正在输入", select_option: "正在选择选项", press_key: "正在按键",
  scroll: "正在滚动", go_back: "正在后退", go_forward: "正在前进", refresh: "正在刷新",
  switch_tab: "正在切换标签页", close_tab: "正在关闭标签页", evaluate: "正在执行页面操作",
  find_text: "正在查找", dropdown_options: "正在读取选项", wait_for: "正在等待页面", screenshot: "截图已完成",
};

/** Reuse the browser HUD asset inside an isolated world of the consented page.
 * CSS coordinates stay local to that page, so the overlay cannot cover Loom's shell.
 */
// A webContents that has never loaded a page (a task's new tab) holds a script
// until a document exists, and the action waiting on the HUD never started the
// navigation that would make one: the first Loom Browser command after a start
// sat there until the Host's 45 s command timeout (live, 2026-11-02). The HUD
// is decoration, so no wait on it may outlast this.
const HUD_SCRIPT_LIMIT_MS = 1500;

function runHudScript(wc: WebContents, code: string): Promise<unknown> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const limit = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("HUD script did not run")), HUD_SCRIPT_LIMIT_MS);
  });
  return Promise.race([wc.executeJavaScriptInIsolatedWorld(WORLD, [{ code }]), limit])
    .finally(() => clearTimeout(timer));
}

export class EmbeddedBrowserHud {
  private source = "";
  private readonly timers = new Map<number, ReturnType<typeof setTimeout>>();
  private readonly captures = new Map<number, number>();
  constructor(private readonly loadSource: () => string | Promise<string> = () => "") {}

  async show(wc: WebContents, action: string, options: { point?: { x: number; y: number }; click?: boolean; settled?: boolean; failed?: boolean } = {}): Promise<void> {
    if (!LABELS[action] || wc.isDestroyed()) return;
    this.clearTimer(wc.id);
    try {
      this.source ||= await this.loadSource();
      if (!this.source) return;
      const payload = {
        title: options.failed ? "浏览器操作未完成" : LABELS[action],
        subtitle: options.failed ? "请查看对话中的操作结果" : "Loom 内置浏览器 · 当前网页",
        source: "内置浏览器 · DOM",
        ...options.point, click: Boolean(options.click),
      };
      await runHudScript(wc, this.source + ";" + HUD + "?.present(" + JSON.stringify(payload) + ");"
        + (this.captures.has(wc.id) ? "{const host=" + HOST + ";if(host)host.style.display='none'}" : ""));
      if (!wc.isDestroyed()) this.timers.set(wc.id, setTimeout(() => { void this.hide(wc); }, options.settled ? 1400 : 30_000));
    } catch { /* A navigation or closed page must not turn a decorative HUD into a failed action. */ }
  }

  private clearTimer(id: number): void {
    const timer = this.timers.get(id);
    if (timer) clearTimeout(timer);
    this.timers.delete(id);
  }

  async hide(wc: WebContents, release = false): Promise<void> {
    this.clearTimer(wc.id);
    if (wc.isDestroyed()) return;
    try { await runHudScript(wc, HUD + (release ? "?.dispose()" : "?.hide(true)")); } catch {}
  }

  async capture<T>(wc: WebContents, take: () => Promise<T>): Promise<T> {
    // Hide the host outright: waiting only for an opacity fade can photograph the HUD.
    let hidden = false;
    this.captures.set(wc.id, (this.captures.get(wc.id) || 0) + 1);
    try {
      await runHudScript(wc, "{const host=" + HOST + ";if(host)host.style.display='none'}");
      hidden = true;
      await new Promise(resolve => setTimeout(resolve, 32));
    } catch { /* A page without an installed HUD needs no cleanup before capture. */ }
    try {
      return await take();
    } finally {
      const remaining = (this.captures.get(wc.id) || 1) - 1;
      if (remaining) this.captures.set(wc.id, remaining);
      else this.captures.delete(wc.id);
      if (!remaining && hidden && !wc.isDestroyed()) {
        try { await runHudScript(wc, "{const host=" + HOST + ";if(host)host.style.removeProperty('display')}"); } catch {}
      }
    }
  }

  dispose(): void {
    for (const timer of this.timers.values()) clearTimeout(timer);
    this.timers.clear();
    this.captures.clear();
  }
}
