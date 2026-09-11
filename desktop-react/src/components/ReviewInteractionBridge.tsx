import { useEffect } from "react";

interface ReviewInteractionBridgeProps {
  onOpen(path?: string): void;
}

function cleanPath(value: string | null | undefined): string {
  return String(value ?? "")
    .trim()
    .replaceAll("\\", "/")
    .replace(/^\.\//, "");
}

function selectionIsActive(): boolean {
  const selection = window.getSelection();
  return Boolean(selection && !selection.isCollapsed && selection.toString().trim());
}

/**
 * Connects transcript affordances to the persistent review pane without
 * threading review callbacks through every message/activity component.
 *
 * File edit rows and changed-file artifacts open the matching file. Clicking a
 * code block opens the review pane without stealing text selection or copy
 * button interactions.
 */
export function ReviewInteractionBridge({ onOpen }: ReviewInteractionBridgeProps) {
  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target || target.closest(".review-workspace")) return;

      const artifactPath = target.closest<HTMLElement>(".turn-artifacts-files code[title]");
      if (artifactPath) {
        onOpen(cleanPath(artifactPath.getAttribute("title") || artifactPath.textContent));
        return;
      }

      const artifactHeader = target.closest<HTMLElement>(".turn-artifacts-header");
      if (artifactHeader) {
        const artifact = artifactHeader.closest<HTMLElement>(".turn-artifacts");
        const pathNode = artifact?.querySelector<HTMLElement>(".turn-artifacts-files code[title]");
        onOpen(cleanPath(pathNode?.getAttribute("title") || pathNode?.textContent));
        return;
      }

      const taskRow = target.closest<HTMLElement>(".task-flow-row");
      const taskPath = taskRow?.querySelector<HTMLElement>(".task-flow-path");
      if (taskPath) {
        onOpen(cleanPath(taskPath.getAttribute("title") || taskPath.textContent));
        return;
      }

      const codeBlock = target.closest<HTMLElement>(".markdown-code-block pre, .markdown-code-block code");
      if (codeBlock) {
        if (target.closest("button, a") || selectionIsActive()) return;
        onOpen();
      }
    };

    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, [onOpen]);

  return null;
}
