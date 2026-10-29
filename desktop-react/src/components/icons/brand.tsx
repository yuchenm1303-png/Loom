import { forwardRef } from "react";
import type { IconProps } from "./base";

/*
 * Brand marks.
 *
 * These are not redrawn glyphs: they are the marks a service is known by, so
 * they are drawn as paths in the brand's own colours.
 *
 * The sign-in card used to paint the Google "G" as a letter with a gradient
 * clipped to the text (`background-clip: text`, `color: transparent`). The
 * portal's liquid lens redraws the page onto a canvas before it refracts it,
 * and a canvas cannot clip a gradient to glyphs: the letter was simply not in
 * the picture, so the icon vanished as soon as the lens mounted on the button.
 * A mark made of paths is redrawn like any other icon.
 */
export const Google = Object.assign(
  forwardRef<SVGSVGElement, IconProps>(function Google(
    { size = 18, className, ...rest },
    ref,
  ) {
    return (
      <svg
        ref={ref}
        xmlns="http://www.w3.org/2000/svg"
        width={size}
        height={size}
        viewBox="0 0 48 48"
        className={className ? `loom-icon ${className}` : "loom-icon"}
        data-icon="google"
        aria-hidden="true"
        {...rest}
      >
        <path
          fill="#FFC107"
          d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"
        />
        <path
          fill="#FF3D00"
          d="m6.306 14.691 6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z"
        />
        <path
          fill="#4CAF50"
          d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238C28.41 35.091 26.298 36 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z"
        />
        <path
          fill="#1976D2"
          d="M43.611 20.083H42V20H24v8h11.303c-.792 2.237-2.231 4.166-4.087 5.571l.003-.002 6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z"
        />
      </svg>
    );
  }),
  { iconName: "google" },
);
