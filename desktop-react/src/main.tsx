import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { BootErrorBoundary } from "./components/BootErrorBoundary";
import "./styles.css";
import "./shell-fix.css";
import "./components/model-panel-overrides.css";
import "./components/reasoning-control.css";
import "./components/sidebar-brand-polish.css";
import "./components/inline-thinking-orb.css";
import "./components/composer-stability.css";
import "./typography-scale.css";

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Loom renderer root element is missing");

createRoot(rootElement).render(
  <StrictMode>
    <BootErrorBoundary>
      <App />
    </BootErrorBoundary>
  </StrictMode>,
);
