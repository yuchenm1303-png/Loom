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

function clearExternalReview(): void {
  window.dispatchEvent(new CustomEvent("loom:review-clear-external"));
}

/**
 * Connects transcript and project affordances to the persistent review pane
 * without threading review callbacks through every message/activity component.
 */
export function ReviewInteractionBridge({ onOpen }: ReviewInteractionBridgeProps) {
  useEffect(() => {
    const handleOpenEvent = (event: Event) => {
      clearExternalReview();
      const detail = (event as CustomEvent<{ path?: string }>).detail;
      const path = cleanPath(detail?.path);
      onOpen(path || undefined);
    };

    const handleProjectDiffEvent = (event: Event) => {
      const detail = (event as CustomEvent<{ path?: string }>).detail;
      const path = cleanPath(detail?.path);
      onOpen(path || undefined);
    };

    const handleClick = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target || target.closest(".review-workspace")) return;

      const artifactPath = target.closest<HTMLElement>(".turn-artifacts-files code[title]");
      if (artifactPath) {
        clearExternalReview();
        onOpen(cleanPath(artifactPath.getAttribute("title") || artifactPath.textContent));
        return;
      }

      const taskRow = target.closest<HTMLElement>(".task-flow-row");
      const taskPath = taskRow?.querySelector<HTMLElement>(".task-flow-path");
      if (taskPath) {
        clearExternalReview();
        onOpen(cleanPath(taskPath.getAttribute("title") || taskPath.textContent));
        return;
      }

      const codeBlock = target.closest<HTMLElement>(".markdown-code-block pre, .markdown-code-block code");
      if (codeBlock) {
        if (target.closest("button, a") || selectionIsActive()) return;
        clearExternalReview();
        onOpen();
      }
    };

    window.addEventListener("loom:review-open", handleOpenEvent);
    window.addEventListener("loom:review-open-diff", handleProjectDiffEvent);
    document.addEventListener("click", handleClick);
    return () => {
      window.removeEventListener("loom:review-open", handleOpenEvent);
      window.removeEventListener("loom:review-open-diff", handleProjectDiffEvent);
      document.removeEventListener("click", handleClick);
    };
  }, [onOpen]);

  return null;
}
