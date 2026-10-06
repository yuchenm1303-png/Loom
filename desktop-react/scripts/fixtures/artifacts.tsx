import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { ArtifactPreviewDock } from "../../src/components/ArtifactPreviewDock";
import type { TranscriptItem } from "../../src/types/loom";
import "../../src/theme.css";
const items = ["test_page.html", "report.md", "test_page.html", "image.png"].map((path, index) => ({
  id: String(index), type: "file_edit", threadId: "thread", turnId: "turn", paths: [path], status: "completed",
})) as TranscriptItem[];
Object.assign(window, { loom: {
  localArtifactPreviewUrl: async () => "data:text/html,<h1>Interactive preview</h1>",
  openLocalArtifact: async (path: string) => { document.body.dataset.opened = path; },
} });
function Fixture() {
  const [open, setOpen] = useState(true);
  return <><button onClick={() => setOpen(true)}>打开产物</button><ArtifactPreviewDock open={open} path="" workspace="workspace" items={items} onClose={() => setOpen(false)} /></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
