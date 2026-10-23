import { createRoot } from "react-dom/client";
import { PortalLiquidCursor } from "./components/PortalLiquidCursor";

// Share the exact same lens engine with the independently hosted homepage.
if (!document.getElementById("loom-cursor-root")) {
  const mount = document.createElement("div");
  mount.id = "loom-cursor-root";
  document.body.appendChild(mount);
  createRoot(mount).render(<PortalLiquidCursor />);
}
