import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { BootErrorBoundary } from "./components/BootErrorBoundary";
import "./styles.css";

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Loom renderer root element is missing");

createRoot(rootElement).render(
  <StrictMode>
    <BootErrorBoundary>
      <App />
    </BootErrorBoundary>
  </StrictMode>,
);
