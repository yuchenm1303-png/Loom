/* The header switch between a conversation and its persistent project (ProjectMode.tsx):
 * 对话 / 织造 (Chat / Weave). Weaving is what a loom does: one objective worked thread by
 * thread, over days. Kept apart from ProjectFlow so the header loads none of the mode.
 *
 * Header controls are quiet and tint is reserved for selection (workspace-surface-refinement.css):
 * a hairline capsule, and the selection's tint on a thumb that travels to the chosen item.
 * The thumb is measured, so the two labels may differ in width; choosing 织造 draws the
 * glyph's weft once. Styles: project-mode-toggle.css. */
import { useLayoutEffect, useRef, useState } from "react";
import { createIcon } from "./icons/base";
import "./project-mode-toggle.css";

const ChatGlyph = createIcon("mode-chat", "ModeChat",
  <path d="M7.9 19.5A8.5 8.5 0 1 0 4.5 16L3 21Z" />);
// Two warp threads and a weft that passes over one and under the other.
const WeaveGlyph = createIcon("mode-weave", "ModeWeave", <>
  <path d="M8 3.5v17" />
  <path d="M16 3.5v17" />
  <path className="li li-weft" d="M3 12c2.5 0 3.5-4 5-4s2.5 8 8 8 2.5-4 5-4" pathLength={1} />
</>);

export function ProjectModeToggle({ value, onChange, lang = "zh" }: { value: "chat" | "project"; onChange(value: "chat" | "project"): void; lang?: "zh" | "en" }) {
  const words = lang === "en" ? { chat: "Chat", project: "Weave", label: "Mode", hint: "Pursue one objective over days, task by task" }
                              : { chat: "对话", project: "织造", label: "工作方式", hint: "一个目标，拆成任务持续推进，独立复审后集成" };
  const items = useRef<Record<string, HTMLButtonElement | null>>({});
  const [thumb, setThumb] = useState<{ x: number; width: number } | null>(null);
  useLayoutEffect(() => {
    const node = items.current[value];
    if (node) setThumb({ x: node.offsetLeft, width: node.offsetWidth });
  }, [value, lang]);
  return (
    <div className="pf-toggle" role="radiogroup" aria-label={words.label} data-at={value}>
      <span className="pf-toggle-thumb" aria-hidden="true" data-ready={thumb ? "" : undefined}
            style={thumb ? { transform: `translateX(${thumb.x}px)`, width: thumb.width } : undefined} />
      {(["chat", "project"] as const).map((option) => {
        const Glyph = option === "chat" ? ChatGlyph : WeaveGlyph;
        return (
          <button key={option} type="button" role="radio" aria-checked={value === option} data-on={value === option || undefined}
                  ref={(node) => { items.current[option] = node; }} title={option === "project" ? words.hint : undefined}
                  onClick={() => onChange(option)}>
            <Glyph size={14} strokeWidth={1.75} aria-hidden="true" />
            <span>{words[option]}</span>
          </button>
        );
      })}
    </div>
  );
}
