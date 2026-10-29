import type { DragEvent } from "react";

/**
 * A conversation carried by drag and drop (from the sidebar or the run panel) to the workspace, where the half it is
 * dropped on decides where it opens. Its own media type, so files dragged in from the desktop are never mistaken for
 * one, and text editors that receive it get its title.
 */
export const THREAD_DRAG_TYPE = "application/x-loom-thread";

export function startThreadDrag(event: DragEvent, threadId: string, title: string): void {
  event.dataTransfer.effectAllowed = "copyMove";
  event.dataTransfer.setData(THREAD_DRAG_TYPE, threadId);
  event.dataTransfer.setData("text/plain", title);
  document.documentElement.dataset.loomThreadDrag = "true";
}

export function endThreadDrag(): void {
  delete document.documentElement.dataset.loomThreadDrag;
}

/** During dragover only the types can be read, not the data. */
export function carriesThread(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes(THREAD_DRAG_TYPE);
}

export function droppedThread(event: DragEvent): string {
  return event.dataTransfer?.getData(THREAD_DRAG_TYPE) ?? "";
}
