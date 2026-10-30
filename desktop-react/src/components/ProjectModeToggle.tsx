/* The header switch between a conversation and its persistent project (ProjectMode.tsx):
 * 对话 / 建造 (Chat / Weave), drawn as glyphs only; the names are the tooltip and label.
 * Kept apart from ProjectFlow so the header loads none of the mode.
 *
 * Header controls are quiet and tint is reserved for selection (workspace-surface-refinement.css):
 * a hairline capsule, and the selection's tint on a thumb that travels to the chosen item.
 * Choosing 建造 draws the code mark's slash once. Styles: project-mode-toggle.css. */
import { useLayoutEffect, useRef, useState } from "react";
import { createIcon } from "./icons/base";
import "./project-mode-toggle.css";

const ChatGlyph = createIcon("mode-chat", "ModeChat",
  <path d="M7.9 19.5A8.5 8.5 0 1 0 4.5 16L3 21Z" />);
// The user's reference: a frame left open at its top-left corner, where a </> sits.
// Fitted to their 120 px crop (shape IoU .89 at the reference's own stroke).
const WeaveGlyph = createIcon("mode-weave", "ModeWeave", <>
  <path d="M2 12.8V17.5a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3" />
  <path d="M4.2 4.4 2.3 7l1.9 2.6" />
  <path className="li li-weft" d="M8.9 4.3 7.3 9.8" pathLength={1} />
  <path d="M11.4 4.4 14.2 7l-2.8 2.6" />
</>);

export function ProjectModeToggle({ value, onChange, lang = "zh" }: { value: "chat" | "project"; onChange(value: "chat" | "project"): void; lang?: "zh" | "en" }) {
  const words = lang === "en" ? { chat: "Chat", project: "Weave", label: "Mode", hint: "Weave · pursue one objective over days, task by task" }
                              : { chat: "对话", project: "建造", label: "工作方式", hint: "建造 · 一个目标拆成任务持续推进，独立复审后集成" };
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
                  ref={(node) => { items.current[option] = node; }} aria-label={words[option]}
                  title={option === "project" ? words.hint : words.chat} onClick={() => onChange(option)}>
            <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
