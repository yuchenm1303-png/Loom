import { Link2 } from "lucide-react";
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
