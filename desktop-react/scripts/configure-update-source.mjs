import fs from "node:fs/promises";
import { validateUpdateSource } from "../dist-electron/updateSource.js";
import { uiContract } from "./ui-contract.mjs";
// Public routing configuration only. Upload credentials never enter the bundle.
const url = validateUpdateSource(process.env.LOOM_UPDATE_BUILD_SOURCE_URL || process.env.LOOM_UPDATE_SOURCE_URL || "");
await fs.writeFile(new URL("../dist-electron/update-source.json", import.meta.url), JSON.stringify({ url }));
await fs.writeFile(new URL("../dist-electron/ui-contract.json", import.meta.url), JSON.stringify({ contract: uiContract() }));
