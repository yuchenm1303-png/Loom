import { forwardRef, type ForwardRefExoticComponent, type ReactNode, type RefAttributes } from "react";
import type { LucideProps } from "lucide-react";

/**
 * Props every Loom icon accepts. They are lucide's props, so an icon from this
 * module can replace a lucide one without touching the call site, plus
 * `spinning` for glyphs that mean "working" (refresh, loader).
 */
export interface IconProps extends Omit<LucideProps, "ref"> {
  spinning?: boolean;
}

export type IconComponent = ForwardRefExoticComponent<IconProps & RefAttributes<SVGSVGElement>> & {
  /** `data-icon` of a redrawn glyph; absent on the lucide icons that were not redrawn. */
  iconName?: string;
};

/** Kept under lucide's name: several screens type their icon maps with it. */
export type LucideIcon = IconComponent;

/**
 * Builds a Loom icon.
 *
 * Every icon here is drawn on lucide's 24-unit grid with round caps and joins,
 * so redrawn glyphs sit beside the lucide ones that were not redrawn. What a
 * redrawn glyph adds is anatomy: its moving parts are separate elements that
 * carry `li` plus a name (`li-lid`, `li-arrow`), so `icon-motion.css` can move
 * "the lid" instead of "the third child of whatever the library drew".
 *
 * `name` is the contract with that stylesheet (`[data-icon="trash"]`).
 */
export function createIcon(name: string, displayName: string, parts: ReactNode): IconComponent {
  const Icon = forwardRef<SVGSVGElement, IconProps>(function LoomIcon(
    { size = 24, color = "currentColor", strokeWidth = 2, absoluteStrokeWidth, spinning, className, children, ...rest },
    ref,
  ) {
    const stroke = absoluteStrokeWidth ? (Number(strokeWidth) * 24) / Number(size) : strokeWidth;
    const labelled = rest["aria-label"] || rest["aria-labelledby"] || rest.role;
    return (
      <svg
        ref={ref}
        xmlns="http://www.w3.org/2000/svg"
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className ? `loom-icon ${className}` : "loom-icon"}
        data-icon={name}
        data-spinning={spinning ? "true" : undefined}
        aria-hidden={labelled ? undefined : true}
        {...rest}
      >
        {parts}
        {children}
      </svg>
    );
  });
  Icon.displayName = displayName;
  // The name `icon-motion.css` knows the glyph by; the icon lab and the tests list glyphs through it.
  return Object.assign(Icon, { iconName: name });
}
