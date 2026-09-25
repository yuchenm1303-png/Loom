from pathlib import Path
import re

root = Path("desktop-react/src/components")

# One route store owns selection for native settings plus portal-backed pages.
(root / "settingsNavigation.ts").write_text(
    '''import { useSyncExternalStore } from "react";

export type NativeSettingsRoute =
  | "general"
  | "appearance"
  | "models"
  | "capabilities"
  | "computer"
  | "browser"
  | "websearch"
  | "terminal"
  | "plugins"
  | "mcp"
  | "skills"
  | "permissions"
  | "shortcuts"
  | "privacy"
  | "developer";

export type SettingsRoute = NativeSettingsRoute | "memory" | "connectors";

let currentRoute: SettingsRoute = "general";
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): SettingsRoute {
  return currentRoute;
}

export function setSettingsRoute(route: SettingsRoute): void {
  if (route === currentRoute) return;
  currentRoute = route;
  for (const listener of listeners) listener();
}

export function useSettingsRoute(): SettingsRoute {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
''',
    encoding="utf-8",
)

# Native SettingsPage participates in the same route state instead of owning a
# competing selected-row state.
p = root / "SettingsPage.tsx"
s = p.read_text(encoding="utf-8")
old = 'import { SettingsWebSearchPanel } from "./SettingsWebSearchPanel";\n'
new = old + 'import { setSettingsRoute, useSettingsRoute } from "./settingsNavigation";\n'
assert old in s and 'useSettingsRoute' not in s
s = s.replace(old, new, 1)

old = 'export function SettingsPage({ runtime, models, running, onClose }: SettingsPageProps) {\n  const [page, setPage] = useState<PageKey>("general");\n'
new = old + '  const activeRoute = useSettingsRoute();\n'
assert old in s
s = s.replace(old, new, 1)

old = '  const navigateToPage = (nextPage: PageKey) => {\n    if (nextPage === page && pageMotion === "idle") return;\n'
new = '  const navigateToPage = (nextPage: PageKey) => {\n    if (nextPage === page && pageMotion === "idle") {\n      setSettingsRoute(nextPage);\n      settingsScrollRef.current?.scrollTo({ top: 0, behavior: "instant" });\n      return;\n    }\n'
assert old in s
s = s.replace(old, new, 1)

old = '    const commitPage = () => {\n      flushSync(() => setPage(nextPage));\n      settingsScrollRef.current?.scrollTo({ top: 0, behavior: "instant" });\n    };\n'
new = '    const commitPage = () => {\n      flushSync(() => {\n        setPage(nextPage);\n        setSettingsRoute(nextPage);\n      });\n      settingsScrollRef.current?.scrollTo({ top: 0, behavior: "instant" });\n    };\n'
assert old in s
s = s.replace(old, new, 1)

anchor = '''  useEffect(() => () => {
    navigationTransitionRef.current += 1;
    if (navigationTimerRef.current !== null) window.clearTimeout(navigationTimerRef.current);
    if (navigationFrameRef.current !== null) cancelAnimationFrame(navigationFrameRef.current);
  }, []);

'''
extra = anchor + '''  useEffect(() => {
    if (activeRoute !== "memory" && activeRoute !== "connectors") return;
    navigationTransitionRef.current += 1;
    if (navigationTimerRef.current !== null) {
      window.clearTimeout(navigationTimerRef.current);
      navigationTimerRef.current = null;
    }
    if (navigationFrameRef.current !== null) {
      cancelAnimationFrame(navigationFrameRef.current);
      navigationFrameRef.current = null;
    }
    setPageMotion("idle");
  }, [activeRoute]);

'''
assert anchor in s
s = s.replace(anchor, extra, 1)

old = 'className={page === item.key ? "active" : ""} aria-current={page === item.key ? "page" : undefined}'
new = 'className={activeRoute === item.key ? "active" : ""} aria-current={activeRoute === item.key ? "page" : undefined}'
assert old in s
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

# Memory keeps its panel implementation, but no longer owns navigation state.
p = root / "SettingsMemoryBridge.tsx"
s = p.read_text(encoding="utf-8")
s = s.replace(
    'import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";',
    'import { useEffect, useLayoutEffect, useMemo, useState } from "react";',
    1,
)
marker = 'import { createPortal } from "react-dom";\n'
assert marker in s
s = s.replace(marker, marker + 'import { setSettingsRoute, useSettingsRoute } from "./settingsNavigation";\n', 1)
start = s.index("export function SettingsMemoryBridge")
replacement = '''export function SettingsMemoryBridge({ threadId, running }: MemorySettingsBridgeProps) {
  const route = useSettingsRoute();
  const open = route === "memory";
  const [navHost] = useState(() => document.createElement("span"));
  const [contentHost] = useState(() => document.createElement("div"));

  useEffect(() => {
    navHost.className = "settings-memory-nav-host";
    contentHost.className = "settings-memory-content-host";

    const syncHosts = () => {
      const shell = document.querySelector<HTMLElement>(".settings-shell");
      const nav = document.querySelector<HTMLElement>(".settings-nav");
      const mainScroll = document.querySelector<HTMLElement>(".settings-main-scroll");
      if (!shell || !nav || !mainScroll) {
        navHost.remove();
        contentHost.remove();
        return;
      }

      const loomSection = Array.from(nav.querySelectorAll<HTMLElement>(":scope > section")).find((section) =>
        section.querySelector(".settings-nav-label")?.textContent?.trim() === "Loom",
      );
      if (loomSection) {
        const permissions = Array.from(loomSection.querySelectorAll<HTMLButtonElement>(":scope > button")).find((button) =>
          button.textContent?.trim() === "Permissions",
        );
        if (navHost.parentElement !== loomSection) {
          if (permissions) loomSection.insertBefore(navHost, permissions);
          else loomSection.appendChild(navHost);
        } else if (permissions && navHost.nextSibling !== permissions) {
          loomSection.insertBefore(navHost, permissions);
        }
      } else {
        navHost.remove();
      }

      if (contentHost.parentElement !== mainScroll) mainScroll.appendChild(contentHost);
    };

    syncHosts();
    const observer = new MutationObserver(syncHosts);
    observer.observe(document.body, { childList: true, subtree: true });

    return () => {
      observer.disconnect();
      document.querySelector(".settings-shell")?.classList.remove("settings-memory-mode");
      navHost.remove();
      contentHost.remove();
    };
  }, [contentHost, navHost]);

  useLayoutEffect(() => {
    document.querySelector<HTMLElement>(".settings-shell")?.classList.toggle("settings-memory-mode", open);
  }, [open]);

  return (
    <>
      {createPortal(
        <button
          type="button"
          className={open ? "active" : ""}
          aria-current={open ? "page" : undefined}
          onClick={() => setSettingsRoute("memory")}
        >
          <BrainCircuit size={16} strokeWidth={1.7} />
          <span>Memory</span>
        </button>,
        navHost,
      )}
      {createPortal(open ? <MemoryPanel threadId={threadId} running={running} /> : null, contentHost)}
    </>
  );
}
'''
s = s[:start] + replacement
p.write_text(s, encoding="utf-8")

# Connectors uses the same route store; no local open flag or click-capture race.
p = root / "SettingsConnectorsBridge.tsx"
p.write_text(
    '''import { Link2 } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { ConnectorLifecycleStatus } from "./ConnectorLifecycleStatus";
import { ConnectorsSettings } from "./ConnectorsSettings";
import { setSettingsRoute, useSettingsRoute } from "./settingsNavigation";
import "./settings-connectors.css";

export function SettingsConnectorsBridge() {
  const route = useSettingsRoute();
  const open = route === "connectors";
  const [running, setRunning] = useState(false);
  const navHost = useMemo(() => {
    const element = document.createElement("div");
    element.className = "settings-connectors-nav-host";
    return element;
  }, []);
  const contentHost = useMemo(() => {
    const element = document.createElement("div");
    element.className = "settings-connectors-content-host";
    return element;
  }, []);

  useEffect(() => {
    const syncHosts = () => {
      const shell = document.querySelector<HTMLElement>(".settings-shell");
      const nav = shell?.querySelector<HTMLElement>(".settings-nav");
      const mainScroll = shell?.querySelector<HTMLElement>(".settings-main-scroll");
      if (!shell || !nav || !mainScroll) {
        navHost.remove();
        contentHost.remove();
        setRunning(false);
        return;
      }

      const footerText = shell.querySelector<HTMLElement>(".settings-sidebar-footer")?.textContent || "";
      setRunning(footerText.includes("Turn active"));

      const integrations = Array.from(nav.querySelectorAll<HTMLElement>(":scope > section")).find((section) =>
        Array.from(section.querySelectorAll<HTMLButtonElement>(":scope > button")).some((button) =>
          button.textContent?.trim() === "MCP",
        ),
      );
      if (integrations) {
        const mcp = Array.from(integrations.querySelectorAll<HTMLButtonElement>(":scope > button")).find((button) =>
          button.textContent?.trim() === "MCP",
        );
        if (navHost.parentElement !== integrations) {
          if (mcp) integrations.insertBefore(navHost, mcp);
          else integrations.appendChild(navHost);
        } else if (mcp && navHost.nextSibling !== mcp) {
          integrations.insertBefore(navHost, mcp);
        }
      } else {
        navHost.remove();
      }

      if (contentHost.parentElement !== mainScroll) mainScroll.appendChild(contentHost);
    };

    syncHosts();
    const observer = new MutationObserver(syncHosts);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });

    return () => {
      observer.disconnect();
      document.querySelector(".settings-shell")?.classList.remove("settings-connectors-mode");
      navHost.remove();
      contentHost.remove();
    };
  }, [contentHost, navHost]);

  useLayoutEffect(() => {
    document.querySelector<HTMLElement>(".settings-shell")?.classList.toggle("settings-connectors-mode", open);
  }, [open]);

  return (
    <>
      {createPortal(
        <button
          type="button"
          className={open ? "active" : ""}
          aria-current={open ? "page" : undefined}
          onClick={() => setSettingsRoute("connectors")}
        >
          <Link2 size={16} strokeWidth={1.7} />
          <span>Connectors</span>
        </button>,
        navHost,
      )}
      {createPortal(open ? (
        <>
          <ConnectorsSettings running={running} />
          <ConnectorLifecycleStatus />
        </>
      ) : null, contentHost)}
    </>
  );
}
''',
    encoding="utf-8",
)

# Stop inferring the selected page by scraping rendered DOM.
p = root / "SettingsComputerLogExport.tsx"
s = p.read_text(encoding="utf-8")
s = s.replace(
    'import { SettingsConnectorsBridge } from "./SettingsConnectorsBridge";\n',
    'import { SettingsConnectorsBridge } from "./SettingsConnectorsBridge";\nimport { useSettingsRoute } from "./settingsNavigation";\n',
    1,
)
s = re.sub(
    r'\nfunction activeSettingsPage\(\): DiagnosticLogKind \| null \{.*?\n\}\n\nfunction labels',
    '\nfunction labels',
    s,
    count=1,
    flags=re.S,
)
old = '''export function SettingsComputerLogExport() {
  const [kind, setKind] = useState<DiagnosticLogKind | null>(null);
  const [busy, setBusy] = useState(false);'''
new = '''export function SettingsComputerLogExport() {
  const route = useSettingsRoute();
  const kind: DiagnosticLogKind | null = route === "computer" ? "computer" : route === "browser" ? "browser" : null;
  const [busy, setBusy] = useState(false);'''
assert old in s
s = s.replace(old, new, 1)
s = re.sub(
    r'\n  useEffect\(\(\) => \{\n    const refresh = \(\) => setKind\(activeSettingsPage\(\)\);.*?\n  \}, \[\]\);\n',
    '\n',
    s,
    count=1,
    flags=re.S,
)
p.write_text(s, encoding="utf-8")

# Delete the previous Memory-only styling workaround. Route state now makes it unnecessary.
p = root / "settings-memory.css"
s = p.read_text(encoding="utf-8")
s, n = re.subn(
    r'/\* Memory is rendered by a bridge on top of the native settings router\..*?\.settings-shell\.settings-memory-mode \.settings-nav > section > button\.active svg \{\n  transform: none;\n\}\n\n',
    '',
    s,
    count=1,
    flags=re.S,
)
assert n == 1
p.write_text(s, encoding="utf-8")
