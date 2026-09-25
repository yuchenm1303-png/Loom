import type { ReactNode, SVGProps } from "react";

export interface ToolGlyphProps extends Omit<SVGProps<SVGSVGElement>, "width" | "height"> {
  size?: number;
}

export type ToolGlyphComponent = (props: ToolGlyphProps) => ReactNode;

function GlyphShell({
  size = 14,
  children,
  viewBox = "0 0 24 24",
  filled = false,
  ...props
}: ToolGlyphProps & { children: ReactNode; viewBox?: string; filled?: boolean }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox={viewBox}
      aria-hidden="true"
      focusable="false"
      fill={filled ? "currentColor" : "none"}
      stroke={filled ? "none" : "currentColor"}
      strokeWidth={filled ? undefined : 1.65}
      strokeLinecap={filled ? undefined : "round"}
      strokeLinejoin={filled ? undefined : "round"}
      {...props}
    >
      {children}
    </svg>
  );
}

/* --------------------------------------------------------------------------
 * Brand marks
 * --------------------------------------------------------------------------
 * These paths are intentionally kept as monochrome vector marks so they inherit
 * Loom's task-row colour system and remain legible in both light and dark
 * themes. The shapes are the real vendor marks; Loom only owns the surrounding
 * badge/chrome.
 *
 * GitHub: Simple Icons
 * OpenAI / Anthropic / DeepSeek / MiniMax / xAI / Gemini: Lobe Icons
 */

export function GitHubGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props} filled>
      <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
    </GlyphShell>
  );
}

export function OpenAIGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props} filled>
      <path d="M9.205 8.658v-2.26c0-.19.072-.333.238-.428l4.543-2.616c.619-.357 1.356-.523 2.117-.523 2.854 0 4.662 2.212 4.662 4.566 0 .167 0 .357-.024.547l-4.71-2.759a.797.797 0 0 0-.856 0l-5.97 3.473zm10.609 8.8V12.06c0-.333-.143-.57-.429-.737l-5.97-3.473 1.95-1.118a.433.433 0 0 1 .476 0l4.543 2.617c1.309.76 2.189 2.378 2.189 3.948 0 1.808-1.07 3.473-2.76 4.163zM7.802 12.703l-1.95-1.142c-.167-.095-.239-.238-.239-.428V5.899c0-2.545 1.95-4.472 4.591-4.472 1 0 1.927.333 2.712.928L8.23 5.067c-.285.166-.428.404-.428.737v6.898zM12 15.128l-2.795-1.57v-3.33L12 8.658l2.795 1.57v3.33L12 15.128zm1.796 7.23c-1 0-1.927-.332-2.712-.927l4.686-2.712c.285-.166.428-.404.428-.737v-6.898l1.974 1.142c.167.095.238.238.238.428v5.233c0 2.545-1.974 4.472-4.614 4.472zm-5.637-5.303-4.544-2.617c-1.308-.761-2.188-2.378-2.188-3.948A4.482 4.482 0 0 1 4.21 6.327v5.423c0 .333.143.571.428.738l5.947 3.449-1.95 1.118a.432.432 0 0 1-.476 0zm-.262 3.9c-2.688 0-4.662-2.021-4.662-4.519 0-.19.024-.38.047-.57l4.686 2.71c.286.167.571.167.856 0l5.97-3.448v2.26c0 .19-.07.333-.237.428l-4.543 2.616c-.619.357-1.356.523-2.117.523zm5.899 2.83a5.947 5.947 0 0 0 5.827-4.756C22.287 18.339 24 15.84 24 13.296c0-1.665-.713-3.282-1.998-4.448.119-.5.19-.999.19-1.498 0-3.401-2.759-5.947-5.946-5.947-.642 0-1.26.095-1.88.31A5.962 5.962 0 0 0 10.205 0a5.947 5.947 0 0 0-5.827 4.757C1.713 5.447 0 7.945 0 10.49c0 1.666.713 3.283 1.998 4.448-.119.5-.19 1-.19 1.499 0 3.401 2.759 5.946 5.946 5.946.642 0 1.26-.095 1.88-.309a5.96 5.96 0 0 0 4.162 1.713z" />
    </GlyphShell>
  );
}

export function AnthropicGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props} filled>
      <path d="M13.827 3.52h3.603L24 20h-3.603l-6.57-16.48zm-7.258 0h3.767L16.906 20h-3.674l-1.343-3.461H5.017l-1.344 3.46H0L6.57 3.522zm4.132 9.959L8.453 7.687 6.205 13.48H10.7z" />
    </GlyphShell>
  );
}

export function GeminiGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props} filled>
      <path d="M20.616 10.835a14.147 14.147 0 0 1-4.45-3.001 14.111 14.111 0 0 1-3.678-6.452.503.503 0 0 0-.975 0 14.134 14.134 0 0 1-3.679 6.452 14.155 14.155 0 0 1-4.45 3.001c-.65.28-1.318.505-2.002.678a.502.502 0 0 0 0 .975c.684.172 1.35.397 2.002.677a14.147 14.147 0 0 1 4.45 3.001 14.112 14.112 0 0 1 3.679 6.453.502.502 0 0 0 .975 0c.172-.685.397-1.351.677-2.003a14.145 14.145 0 0 1 3.001-4.45 14.113 14.113 0 0 1 6.453-3.678.503.503 0 0 0 0-.975 13.245 13.245 0 0 1-2.003-.678z" />
    </GlyphShell>
  );
}

export function DeepSeekGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props} filled>
      <path d="M23.748 4.482c-.254-.124-.364.113-.512.234-.051.039-.094.09-.137.136-.372.397-.806.657-1.373.626-.829-.046-1.537.214-2.163.848-.133-.782-.575-1.248-1.247-1.548-.352-.156-.708-.311-.955-.65-.172-.241-.219-.51-.305-.774-.055-.16-.11-.323-.293-.35-.2-.031-.278.136-.356.276-.313.572-.434 1.202-.422 1.84.027 1.436.633 2.58 1.838 3.393.137.093.172.187.129.323-.082.28-.18.552-.266.833-.055.179-.137.217-.329.14a5.526 5.526 0 0 1-1.736-1.18c-.857-.828-1.631-1.742-2.597-2.458a11.365 11.365 0 0 0-.689-.471c-.985-.957.13-1.743.388-1.836.27-.098.093-.432-.779-.428-.872.004-1.67.295-2.687.684a3.055 3.055 0 0 1-.465.137 9.597 9.597 0 0 0-2.883-.102c-1.885.21-3.39 1.102-4.497 2.623C.082 8.606-.231 10.684.152 12.85c.403 2.284 1.569 4.175 3.36 5.653 1.858 1.533 3.997 2.284 6.438 2.14 1.482-.085 3.133-.284 4.994-1.86.47.234.962.327 1.78.397.63.059 1.236-.03 1.705-.128.735-.156.684-.837.419-.961-2.155-1.004-1.682-.595-2.113-.926 1.096-1.296 2.746-2.642 3.392-7.003.05-.347.007-.565 0-.845-.004-.17.035-.237.23-.256a4.173 4.173 0 0 0 1.545-.475c1.396-.763 1.96-2.015 2.093-3.517.02-.23-.004-.467-.247-.588zM11.581 18c-2.089-1.642-3.102-2.183-3.52-2.16-.392.024-.321.471-.235.763.09.288.207.486.371.739.114.167.192.416-.113.603-.673.416-1.842-.14-1.897-.167-1.361-.802-2.5-1.86-3.301-3.307-.774-1.393-1.224-2.887-1.298-4.482-.02-.386.093-.522.477-.592a4.696 4.696 0 0 1 1.529-.039c2.132.312 3.946 1.265 5.468 2.774.868.86 1.525 1.887 2.202 2.891.72 1.066 1.494 2.082 2.48 2.914.348.292.625.514.891.677-.802.09-2.14.11-3.054-.614zm1-6.44a.306.306 0 0 1 .415-.287.302.302 0 0 1 .2.288.306.306 0 0 1-.31.307.303.303 0 0 1-.304-.308zm3.11 1.596c-.2.081-.399.151-.59.16a1.245 1.245 0 0 1-.798-.254c-.274-.23-.47-.358-.552-.758a1.73 1.73 0 0 1 .016-.588c.07-.327-.008-.537-.239-.727-.187-.156-.426-.199-.688-.199a.559.559 0 0 1-.254-.078c-.11-.054-.2-.19-.114-.358.028-.054.16-.186.192-.21.356-.202.767-.136 1.146.016.352.144.618.408 1.001.782.391.451.462.576.685.914.176.265.336.537.445.848.067.195-.019.354-.25.452z" />
    </GlyphShell>
  );
}

export function MiniMaxGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props} filled>
      <path d="M16.278 2c1.156 0 2.093.927 2.093 2.07v12.501a.74.74 0 0 0 .744.709.74.74 0 0 0 .743-.709V9.099a2.06 2.06 0 0 1 2.071-2.049A2.06 2.06 0 0 1 24 9.1v6.561a.649.649 0 0 1-.652.645.649.649 0 0 1-.653-.645V9.1a.762.762 0 0 0-.766-.758.762.762 0 0 0-.766.758v7.472a2.037 2.037 0 0 1-2.048 2.026 2.037 2.037 0 0 1-2.048-2.026v-12.5a.785.785 0 0 0-.788-.753.785.785 0 0 0-.789.752l-.001 15.904A2.037 2.037 0 0 1 13.441 22a2.037 2.037 0 0 1-2.048-2.026V18.04c0-.356.292-.645.652-.645.36 0 .652.289.652.645v1.934c0 .263.142.506.372.638.23.131.514.131.744 0a.734.734 0 0 0 .372-.638V4.07c0-1.143.937-2.07 2.093-2.07zm-5.674 0c1.156 0 2.093.927 2.093 2.07v11.523a.648.648 0 0 1-.652.645.648.648 0 0 1-.652-.645V4.07a.785.785 0 0 0-.789-.78.785.785 0 0 0-.789.78v14.013a2.06 2.06 0 0 1-2.07 2.048 2.06 2.06 0 0 1-2.071-2.048V9.1a.762.762 0 0 0-.766-.758.762.762 0 0 0-.766.758v3.8a2.06 2.06 0 0 1-2.071 2.049A2.06 2.06 0 0 1 0 12.9v-1.378c0-.357.292-.646.652-.646.36 0 .653.29.653.646V12.9c0 .418.343.757.766.757s.766-.339.766-.757V9.099a2.06 2.06 0 0 1 2.07-2.048 2.06 2.06 0 0 1 2.071 2.048v8.984c0 .419.343.758.767.758.423 0 .766-.339.766-.758V4.07c0-1.143.937-2.07 2.093-2.07z" />
    </GlyphShell>
  );
}

export function XAIGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props} filled>
      <path d="M6.469 8.776 16.512 23h-4.464L2.005 8.776H6.47zm-.004 7.9 2.233 3.164L6.467 23H2l4.465-6.324zM22 2.582V23h-3.659V7.764L22 2.582zM22 1l-9.952 14.095-2.233-3.163L17.533 1H22z" />
    </GlyphShell>
  );
}

/* --------------------------------------------------------------------------
 * Loom capability marks
 * --------------------------------------------------------------------------
 * These are intentionally custom rather than vendor-branded: they describe a
 * capability, not a company. Their geometry shares a 24px grid, rounded joins
 * and restrained 1.65px strokes so mixed workflows feel like one product.
 */

export function SentinelXGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <path d="M12 2.9 19 5.8v5.3c0 4.35-2.7 7.75-7 10-4.3-2.25-7-5.65-7-10V5.8L12 2.9Z" />
      <path d="m8.4 9.2 2.05 2.05L8.4 13.3" />
      <path d="M12.2 13.3h3.35" />
    </GlyphShell>
  );
}

export function TerminalGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <rect x="3.15" y="4.2" width="17.7" height="15.6" rx="3.1" />
      <path d="M3.5 8.1h17" />
      <path d="m7.15 11.05 2.1 1.9-2.1 1.9" />
      <path d="M11.4 15h4.35" />
    </GlyphShell>
  );
}

export function FileEditGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <path d="M6.2 3.4h7.35l4.25 4.25v12.9H6.2a2 2 0 0 1-2-2V5.4a2 2 0 0 1 2-2Z" />
      <path d="M13.55 3.8v4.1h4.05" />
      <path d="M8 12.15h3.5M9.75 10.4v3.5" />
      <path d="M13.85 15.65h3.15" />
    </GlyphShell>
  );
}

export function BrowserGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <rect x="2.9" y="4" width="18.2" height="16" rx="3.1" />
      <path d="M3.3 8.2h17.4" />
      <circle cx="6.25" cy="6.15" r=".55" fill="currentColor" stroke="none" />
      <circle cx="8.35" cy="6.15" r=".55" fill="currentColor" stroke="none" />
      <path d="m12.4 11 5.1 2.15-2.05.8.85 2.05-1.55.65-.82-2.02-1.53 1.45V11Z" />
    </GlyphShell>
  );
}

export function ComputerGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <rect x="3" y="3.6" width="18" height="12.5" rx="2.8" />
      <path d="M8.5 20.4h7M12 16.25v4" />
      <path d="m11.6 7.4 5.25 2.15-2.15.8.9 2.15-1.55.65-.87-2.1-1.58 1.5V7.4Z" />
    </GlyphShell>
  );
}

export function WorkspaceGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <path d="M3.4 7.4h6l1.8 2h9.4v8.9a2.15 2.15 0 0 1-2.15 2.15H5.55A2.15 2.15 0 0 1 3.4 18.3V7.4Z" />
      <path d="M3.4 7.4V5.8a2.15 2.15 0 0 1 2.15-2.15h4.1l1.8 2h6.95c1.2 0 2.2.98 2.2 2.2V9.4" />
      <path d="M8.2 14.8h7.6" />
    </GlyphShell>
  );
}

export function SearchGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <circle cx="10.3" cy="10.3" r="5.9" />
      <path d="m14.65 14.65 5 5" />
      <path d="M5 10.3h10.6M10.3 4.4c1.8 1.75 2.7 3.72 2.7 5.9 0 2.2-.9 4.17-2.7 5.9-1.8-1.73-2.7-3.7-2.7-5.9 0-2.18.9-4.15 2.7-5.9Z" opacity=".66" />
    </GlyphShell>
  );
}

export function MemoryGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <rect x="5" y="5" width="14" height="14" rx="3.2" />
      <path d="M8.3 2.7v2.2M12 2.7v2.2M15.7 2.7v2.2M8.3 19.1v2.2M12 19.1v2.2M15.7 19.1v2.2M2.7 8.3h2.2M2.7 12h2.2M2.7 15.7h2.2M19.1 8.3h2.2M19.1 12h2.2M19.1 15.7h2.2" />
      <path d="M9 12a3 3 0 1 1 6 0v3H9v-3Z" />
    </GlyphShell>
  );
}

export function SkillGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <path d="m12 3.2 1.25 3.55L16.8 8l-3.55 1.25L12 12.8l-1.25-3.55L7.2 8l3.55-1.25L12 3.2Z" />
      <path d="m18.4 12.2.72 2.05 2.03.72-2.03.72-.72 2.05-.72-2.05-2.03-.72 2.03-.72.72-2.05Z" />
      <path d="m6 14.3.8 2.25 2.25.8-2.25.8L6 20.4l-.8-2.25-2.25-.8 2.25-.8L6 14.3Z" />
    </GlyphShell>
  );
}

export function CalculatorGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <rect x="5" y="2.9" width="14" height="18.2" rx="3" />
      <rect x="7.8" y="5.4" width="8.4" height="3.4" rx="1" />
      <path d="M8 12h1M11.5 12h1M15 12h1M8 15.5h1M11.5 15.5h1M15 15.5h1M8 19h1M11.5 19h4.5" />
    </GlyphShell>
  );
}

export function ApprovalGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <path d="M12 2.9 19 5.8v5.3c0 4.35-2.7 7.75-7 10-4.3-2.25-7-5.65-7-10V5.8L12 2.9Z" />
      <path d="m8.3 11.9 2.25 2.2 5.15-5.1" />
    </GlyphShell>
  );
}

export function SubAgentGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <circle cx="12" cy="6" r="2.55" />
      <circle cx="6" cy="17.7" r="2.2" />
      <circle cx="18" cy="17.7" r="2.2" />
      <path d="M10.7 8.2 7.2 15.6M13.3 8.2l3.5 7.4M8.2 17.7h7.6" />
    </GlyphShell>
  );
}

export function AutomationGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <circle cx="5" cy="6" r="2" />
      <circle cx="19" cy="18" r="2" />
      <path d="M7 6h4.2c3.2 0 4.8 1.8 4.8 5.1v.8c0 2.65 1 4.2 3 4.2" />
      <path d="m10.6 3.9 2.1 2.1-2.1 2.1M15.2 13.9l2.1 2.1-2.1 2.1" />
    </GlyphShell>
  );
}

export function MCPGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <path d="M7 7.1 12 4l5 3.1v5.8L12 16l-5-3.1V7.1Z" />
      <path d="m7.2 7.2 4.8 3 4.8-3M12 10.2V16" />
      <circle cx="5" cy="18.3" r="1.7" />
      <circle cx="19" cy="18.3" r="1.7" />
      <path d="m10.65 15.2-4.3 2.25M13.35 15.2l4.3 2.25" />
    </GlyphShell>
  );
}

export function GenericToolGlyph(props: ToolGlyphProps) {
  return (
    <GlyphShell {...props}>
      <circle cx="12" cy="12" r="3" />
      <circle cx="12" cy="4.2" r="1.55" />
      <circle cx="19.2" cy="12" r="1.55" />
      <circle cx="12" cy="19.8" r="1.55" />
      <circle cx="4.8" cy="12" r="1.55" />
      <path d="M12 5.8v3.1M15.1 12h2.55M12 15.1v3.15M8.9 12H6.35" />
    </GlyphShell>
  );
}
