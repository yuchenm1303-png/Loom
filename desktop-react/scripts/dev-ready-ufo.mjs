import path from "node:path";
import { fileURLToPath } from "node:url";

import { main } from "./dev-ready.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "../..");

process.env.LOOM_UFO_SIDECAR ||= path.join(
  repoRoot,
  "app",
  "agent_runtime",
  "ufo_sidecar_supervisor.py",
);

main(["--mode=ufo"]);
