import { memo, useLayoutEffect, useRef, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import "./transcript-edge-scrollbar.css";

/** A native scroll lane at the window edge, synchronized with the conversation. */
export const TranscriptEdgeScrollbar = memo(function TranscriptEdgeScrollbar({ scrollerRef }: {
  scrollerRef: RefObject<HTMLDivElement | null>;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const extentRef = useRef<HTMLDivElement>(null);
  const { language } = useI18n();

  useLayoutEffect(() => {
    const scroller = scrollerRef.current, track = trackRef.current, extent = extentRef.current;
    const shell = scroller?.closest<HTMLElement>(".app-shell.workspace-panels");
    if (!scroller || !track || !extent || !shell) return;
    let syncedTop = 0;
    let forwardedTop: number | null = null;
    let pointerActive = false;
    const start = () => scroller.dispatchEvent(new CustomEvent("loom:scrollbar-drag-start"));
    const finish = () => {
      pointerActive = false;
      scroller.dispatchEvent(new CustomEvent("loom:scrollbar-drag-end"));
    };
    const syncPosition = () => {
      if (Math.abs(track.scrollTop - scroller.scrollTop) >= 0.5) track.scrollTop = scroller.scrollTop;
      syncedTop = track.scrollTop;
    };
    const onSourceScroll = () => {
      if (forwardedTop !== null && Math.abs(scroller.scrollTop - forwardedTop) < 0.5) {
        forwardedTop = null;
        return;
      }
      forwardedTop = null;
      syncPosition();
    };
    const update = () => {
      const rect = scroller.getBoundingClientRect();
      track.hidden = shell.classList.contains("is-settings-obscured")
        || scroller.scrollHeight <= scroller.clientHeight + 1
        || !scroller.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
      if (track.hidden) return;
      const scale = rect.height / scroller.offsetHeight || 1;
      const height = `${scroller.clientHeight}px`, contentHeight = `${scroller.scrollHeight}px`;
      const resized = track.style.height !== height || extent.style.height !== contentHeight;
      track.style.top = `${rect.top / scale}px`;
      track.style.height = height;
      extent.style.height = contentHeight;
      if (resized) syncPosition();
    };
    const onScroll = () => {
      // A copied offset is an echo, not a gesture that should detach live follow.
      if (Math.abs(track.scrollTop - syncedTop) < 0.5) return;
      start();
      scroller.scrollTop = track.scrollTop;
      forwardedTop = scroller.scrollTop;
      syncedTop = track.scrollTop;
      if (!pointerActive) finish();
    };
    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      pointerActive = true;
      start();
    };
    const onPointerUp = () => { if (pointerActive) finish(); };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.metaKey) return;
      const page = scroller.clientHeight * 0.9;
      const targets: Record<string, number> = {
        Home: 0, End: scroller.scrollHeight,
        PageUp: scroller.scrollTop - page, PageDown: scroller.scrollTop + page,
        ArrowUp: scroller.scrollTop - 40, ArrowDown: scroller.scrollTop + 40,
        " ": scroller.scrollTop + (event.shiftKey ? -page : page),
      };
      if (!(event.key in targets)) return;
      event.preventDefault();
      start();
      scroller.scrollTop = targets[event.key];
      forwardedTop = scroller.scrollTop;
      syncPosition();
      finish();
    };
    const onWheel = (event: WheelEvent) => {
      scroller.dispatchEvent(new WheelEvent("wheel", {
        deltaX: event.deltaX, deltaY: event.deltaY, deltaMode: event.deltaMode, bubbles: true,
      }));
    };
    const resize = new ResizeObserver(update);
    resize.observe(scroller);
    const content = scroller.querySelector(".transcript");
    if (content) resize.observe(content);
    const visibility = new MutationObserver(update);
    visibility.observe(shell, { attributes: true, attributeFilter: ["class"] });
    scroller.dataset.edgeScrollbar = "";
    scroller.addEventListener("scroll", onSourceScroll, { passive: true });
    track.addEventListener("scroll", onScroll, { passive: true });
    track.addEventListener("wheel", onWheel, { passive: true });
    track.addEventListener("pointerdown", onPointerDown);
    track.addEventListener("keydown", onKeyDown);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
    window.addEventListener("resize", update);
    window.addEventListener("loom:panel-layout-commit", update);
    update();
    return () => {
      resize.disconnect(); visibility.disconnect();
      delete scroller.dataset.edgeScrollbar;
      scroller.removeEventListener("scroll", onSourceScroll);
      track.removeEventListener("scroll", onScroll);
      track.removeEventListener("wheel", onWheel);
      track.removeEventListener("pointerdown", onPointerDown);
      track.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      window.removeEventListener("resize", update);
      window.removeEventListener("loom:panel-layout-commit", update);
    };
  }, [scrollerRef]);

  return createPortal(
    <div ref={trackRef} className="transcript-edge-scrollbar" hidden tabIndex={0}
      role="region" aria-label={language === "zh-CN" ? "对话滚动条" : "Conversation scrollbar"}>
      <div ref={extentRef} aria-hidden="true" />
    </div>, document.body,
  );
});
