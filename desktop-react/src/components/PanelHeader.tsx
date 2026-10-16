import type { ReactNode } from "react";
import { X } from "./icons";

export interface PanelHeaderProps {
  /** Legacy hook classes stay on the element: the window rail's drag-region cut-outs, tours and tests find a header by them. */
  className?: string;
  mark?: ReactNode;
  markClassName?: string;
  title: ReactNode;
  /** Tooltip for a title that is cut with an ellipsis. */
  titleHint?: string;
  subtitle?: ReactNode;
  subtitleHint?: string;
  /** Renders the subtitle as a path (monospace). */
  subtitleCode?: boolean;
  meta?: ReactNode;
  metaClassName?: string;
  /** Buttons that go before the close button; give them `pl-icon-button`. */
  actions?: ReactNode;
  actionsClassName?: string;
  closeLabel?: string;
  closeClassName?: string;
  onClose?(): void;
}

/**
 * The one header of Loom's panels (components/panel-language.css): a row as high
 * as the window rail with a mark, a title over a subtitle, a meta slot, actions
 * and a single close button. Every dock, the palette and the workbench use it,
 * so a panel cannot grow a header of its own.
 */
export function PanelHeader({
  className, mark, markClassName, title, titleHint, subtitle, subtitleHint, subtitleCode,
  meta, metaClassName, actions, actionsClassName, closeLabel, closeClassName, onClose,
}: PanelHeaderProps) {
  const join = (...names: Array<string | undefined | false>) => names.filter(Boolean).join(" ");
  return (
    <header className={join("pl-header", className)}>
      {mark ? <span className={join("pl-mark", markClassName)}>{mark}</span> : null}
      <div className="pl-heading">
        <strong className="pl-title" title={titleHint}>{title}</strong>
        {subtitle ? (subtitleCode
          ? <code className="pl-subtitle" title={subtitleHint}>{subtitle}</code>
          : <span className="pl-subtitle" title={subtitleHint}>{subtitle}</span>) : null}
      </div>
      {meta ? <div className={join("pl-meta", metaClassName)}>{meta}</div> : null}
      {actions || onClose ? (
        <div className={join("pl-actions", actionsClassName)}>
          {actions}
          {onClose ? (
            <button
              type="button"
              className={join("pl-icon-button", "pl-close", closeClassName)}
              onClick={onClose}
              title={closeLabel}
              aria-label={closeLabel}
            >
              <X size={16} strokeWidth={1.8} />
            </button>
          ) : null}
        </div>
      ) : null}
    </header>
  );
}
