import type { ReactNode } from "react";

/**
 * The one empty state of Loom's panels (components/panel-language.css): the panel's own glyph
 * on a quiet tile held by one fading hairline frame, a title, a sentence and, if there is a
 * family of things to name, a row of chips. It does not move: an empty panel is the place nothing should be asking
 * for the eye (and a static one costs no frame while the conversation streams).
 */
export function PanelEmpty({ icon, title, copy, chips, className }: {
  icon: ReactNode;
  title: ReactNode;
  copy?: ReactNode;
  chips?: readonly string[];
  className?: string;
}) {
  return (
    <div className={["pl-empty", className].filter(Boolean).join(" ")}>
      <div className="pl-empty-art" aria-hidden="true">
        <span className="pl-empty-core">{icon}</span>
      </div>
      <strong className="pl-empty-title">{title}</strong>
      {copy ? <p className="pl-empty-copy">{copy}</p> : null}
      {chips?.length ? <div className="pl-empty-chips" aria-hidden="true">{chips.map((chip) => <span key={chip}>{chip}</span>)}</div> : null}
    </div>
  );
}
