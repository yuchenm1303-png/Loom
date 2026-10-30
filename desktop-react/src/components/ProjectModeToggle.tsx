/* The header switch between a conversation and its persistent project (ProjectMode.tsx).
 * Kept apart from ProjectFlow so the header does not load the mode's code and styles. */
import "./project-mode-toggle.css";

/** The header switch between the conversation and its persistent project. */
export function ProjectModeToggle({ value, onChange, lang = "zh" }: { value: "chat" | "project"; onChange(value: "chat" | "project"): void; lang?: "zh" | "en" }) {
  const words = lang === "en" ? { chat: "Chat", project: "Persistent", label: "Mode" } : { chat: "对话", project: "持续", label: "工作方式" };
  return (
    <div className="pf-toggle" role="radiogroup" aria-label={words.label} data-at={value}>
      <span className="pf-toggle-thumb" aria-hidden="true" />
      {(["chat", "project"] as const).map((option) => (
        <button key={option} type="button" role="radio" aria-checked={value === option} data-on={value === option || undefined}
                onClick={() => onChange(option)}>{words[option]}</button>
      ))}
    </div>
  );
}
