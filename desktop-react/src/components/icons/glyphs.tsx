/*
 * Loom's redrawn icons.
 *
 * Each glyph is drawn on the 24-unit grid with round caps and joins. Most keep
 * the silhouette of the lucide icon of the same name (lucide is ISC licensed,
 * (c) Lucide Contributors) and differ only in how they are built: the parts that
 * move are separate elements, marked `li` plus a name. `icon-motion.css` is the
 * only file that knows what those names do; nothing here moves by itself.
 *
 * Part names are the motion vocabulary:
 *   li-all     the whole glyph, turned or leaned as one piece
 *   li-lid     something that opens (trash lid, archive lid, shackle)
 *   li-arrow   something that leaves in a direction (the arrow of a download)
 *   li-tick    a mark that is drawn (stroke-dash), at rest it is simply there
 *   li-ink     a mark that only exists while the glyph is engaged
 *
 * The glyphs that are not redrawn here are re-exported from lucide in index.ts
 * and stay still on purpose: motion is for icons that have something to say.
 */
import { createIcon } from "./base";

/* ── Disclosure and direction ─────────────────────────────────────────────── */

export const ChevronRight = createIcon("chevron-right", "ChevronRight",
  <g className="li li-all"><path d="m9 18 6-6-6-6" /></g>);
export const ChevronLeft = createIcon("chevron-left", "ChevronLeft",
  <g className="li li-all"><path d="m15 18-6-6 6-6" /></g>);
export const ChevronUp = createIcon("chevron-up", "ChevronUp",
  <g className="li li-all"><path d="m18 15-6-6-6 6" /></g>);
export const ChevronDown = createIcon("chevron-down", "ChevronDown",
  <g className="li li-all"><path d="m6 9 6 6 6-6" /></g>);

export const ArrowRight = createIcon("arrow-right", "ArrowRight", <>
  <path className="li li-shaft" d="M5 12h14" />
  <path className="li li-head" d="m12 5 7 7-7 7" />
</>);
export const ArrowLeft = createIcon("arrow-left", "ArrowLeft", <>
  <path className="li li-shaft" d="M19 12H5" />
  <path className="li li-head" d="m12 19-7-7 7-7" />
</>);
export const ArrowUp = createIcon("arrow-up", "ArrowUp", <>
  <path className="li li-shaft" d="M12 19V5" />
  <path className="li li-head" d="m5 12 7-7 7 7" />
</>);
export const ArrowUpRight = createIcon("arrow-up-right", "ArrowUpRight", <>
  <path className="li li-shaft" d="M7 17 17 7" />
  <path className="li li-head" d="M7 7h10v10" />
</>);

export const ExternalLink = createIcon("external-link", "ExternalLink", <>
  <g className="li li-arrow"><path d="M15 3h6v6" /><path d="M10 14 21 3" /></g>
  <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
</>);

export const Maximize2 = createIcon("maximize", "Maximize2", <>
  <g className="li li-out-a"><polyline points="15 3 21 3 21 9" /><path d="M21 3l-7 7" /></g>
  <g className="li li-out-b"><polyline points="9 21 3 21 3 15" /><path d="M3 21l7-7" /></g>
</>);

/* ── Add, close, confirm ──────────────────────────────────────────────────── */

export const Plus = createIcon("plus", "Plus",
  <g className="li li-all"><path d="M5 12h14" /><path d="M12 5v14" /></g>);
export const X = createIcon("x", "X",
  <g className="li li-all"><path d="M18 6 6 18" /><path d="m6 6 12 12" /></g>);
export const Check = createIcon("check", "Check",
  <path className="li li-tick" pathLength={1} d="m4 12 5 5L20 6" />);

export const CheckCircle2 = createIcon("circle-check", "CheckCircle2", <>
  <circle cx="12" cy="12" r="10" />
  <path className="li li-tick" pathLength={1} d="m9 12 2 2 4-4" />
</>);

/* ── Editing ──────────────────────────────────────────────────────────────── */

export const Copy = createIcon("copy", "Copy", <>
  <path className="li li-back" d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
  <rect className="li li-front" width="14" height="14" x="8" y="8" rx="2" ry="2" />
</>);

export const Pencil = createIcon("pencil", "Pencil", <g className="li li-all">
  <path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" />
  <path d="m15 5 4 4" />
</g>);

/** The new-conversation mark: a page and a pen. Engaged, the pen writes a line. */
export const SquarePen = createIcon("compose", "SquarePen", <>
  <path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
  <path className="li li-ink" pathLength={1} d="M7.5 18.2h4.6" />
  <path className="li li-pen" d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z" />
</>);

export const Trash2 = createIcon("trash", "Trash2", <>
  <g className="li li-lid"><path d="M3 6h18" /><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" /></g>
  <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
  <path d="M10 11v6" /><path d="M14 11v6" />
</>);

export const Pin = createIcon("pin", "Pin", <>
  <path className="li li-needle" d="M12 17v5" />
  <path className="li li-head" d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" />
</>);
export const PinOff = createIcon("pin-off", "PinOff", <>
  <path className="li li-needle" d="M12 17v5" />
  <g className="li li-head">
    <path d="M15 9.34V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H7.89" />
    <path d="M9 9v1.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h11" />
  </g>
  <path className="li li-slash" pathLength={1} d="m2 2 20 20" />
</>);

export const Archive = createIcon("archive", "Archive", <>
  <rect className="li li-lid" width="20" height="5" x="2" y="3" rx="1" />
  <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" />
  <path d="M10 12h4" />
</>);
export const ArchiveRestore = createIcon("archive-restore", "ArchiveRestore", <>
  <rect className="li li-lid" width="20" height="5" x="2" y="3" rx="1" />
  <path d="M4 8v11a2 2 0 0 0 2 2h2" />
  <path d="M20 8v11a2 2 0 0 1-2 2h-2" />
  <g className="li li-arrow"><path d="m9 15 3-3 3 3" /><path d="M12 12v9" /></g>
</>);

export const Ellipsis = createIcon("ellipsis", "Ellipsis", <>
  <circle className="li li-dot" cx="5" cy="12" r="1" />
  <circle className="li li-dot li-dot-2" cx="12" cy="12" r="1" />
  <circle className="li li-dot li-dot-3" cx="19" cy="12" r="1" />
</>);

/* ── Find, refresh, wait ──────────────────────────────────────────────────── */

export const Search = createIcon("search", "Search", <g className="li li-all">
  <circle cx="11" cy="11" r="8" />
  <path d="m21 21-4.3-4.3" />
</g>);

export const RefreshCw = createIcon("refresh", "RefreshCw", <g className="li li-all">
  <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
  <path d="M21 3v5h-5" />
  <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
  <path d="M8 16H3v5" />
</g>);
export const RotateCw = createIcon("rotate-cw", "RotateCw", <g className="li li-all">
  <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
  <path d="M21 3v5h-5" />
</g>);
export const RotateCcw = createIcon("rotate-ccw", "RotateCcw", <g className="li li-all">
  <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
  <path d="M3 3v5h5" />
</g>);

/** Always turning: a loader is only ever drawn while something is working. */
export const LoaderCircle = createIcon("loader", "LoaderCircle", <path d="M21 12a9 9 0 1 1-6.219-8.56" />);
export const Loader2 = LoaderCircle;

/* ── Send and receive ─────────────────────────────────────────────────────── */

export const Download = createIcon("download", "Download", <>
  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
  <g className="li li-arrow"><polyline points="7 10 12 15 17 10" /><path d="M12 15V3" /></g>
</>);
export const Upload = createIcon("upload", "Upload", <>
  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
  <g className="li li-arrow"><polyline points="17 8 12 3 7 8" /><path d="M12 3v12" /></g>
</>);
export const LogOut = createIcon("log-out", "LogOut", <>
  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
  <g className="li li-arrow"><polyline points="16 17 21 12 16 7" /><path d="M21 12H9" /></g>
</>);
export const Send = createIcon("send", "Send", <g className="li li-all">
  <path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" />
  <path d="m21.854 2.147-10.94 10.939" />
</g>);
export const Reply = createIcon("reply", "Reply", <g className="li li-all">
  <polyline points="9 17 4 12 9 7" />
  <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
</g>);
export const Play = createIcon("play", "Play",
  <g className="li li-all"><polygon points="6 3 20 12 6 21 6 3" /></g>);
export const Square = createIcon("square", "Square",
  <rect width="18" height="18" x="3" y="3" rx="2" />);

/* ── Feedback ─────────────────────────────────────────────────────────────── */

export const ThumbsUp = createIcon("thumbs-up", "ThumbsUp", <g className="li li-all">
  <path d="M7 10v12" />
  <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
</g>);
export const ThumbsDown = createIcon("thumbs-down", "ThumbsDown", <g className="li li-all">
  <path d="M17 14V2" />
  <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
</g>);

export const Sparkles = createIcon("sparkles", "Sparkles", <>
  <path className="li li-star" d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
  <g className="li li-spark-a"><path d="M20 3v4" /><path d="M22 5h-4" /></g>
  <g className="li li-spark-b"><path d="M4 17v2" /><path d="M5 18H3" /></g>
</>);

/* ── Seeing and securing ──────────────────────────────────────────────────── */

export const Eye = createIcon("eye", "Eye", <>
  <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
  <circle className="li li-pupil" cx="12" cy="12" r="3" />
</>);
export const EyeOff = createIcon("eye-off", "EyeOff", <>
  <path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49" />
  <path d="M14.084 14.158a3 3 0 0 1-4.242-4.242" />
  <path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143" />
  <path className="li li-slash" pathLength={1} d="m2 2 20 20" />
</>);

export const Lock = createIcon("lock", "Lock", <>
  <rect width="18" height="11" x="3" y="11" rx="2" ry="2" />
  <path className="li li-lid" d="M7 11V7a5 5 0 0 1 10 0v4" />
</>);
export const LockKeyhole = createIcon("lock-keyhole", "LockKeyhole", <>
  <circle cx="12" cy="16" r="1" />
  <rect x="3" y="10" width="18" height="12" rx="2" />
  <path className="li li-lid" d="M7 10V7a5 5 0 0 1 10 0v3" />
</>);
export const KeyRound = createIcon("key", "KeyRound", <g className="li li-all">
  <path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z" />
  <circle cx="16.5" cy="7.5" r=".5" fill="currentColor" />
</g>);
export const ShieldCheck = createIcon("shield-check", "ShieldCheck", <>
  <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
  <path className="li li-tick" pathLength={1} d="m9 12 2 2 4-4" />
</>);

/* ── Places and things ────────────────────────────────────────────────────── */

export const Folder = createIcon("folder", "Folder", <g className="li li-all">
  <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
</g>);
export const FolderOpen = createIcon("folder-open", "FolderOpen", <g className="li li-all">
  <path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2" />
</g>);
export const FolderPlus = createIcon("folder-plus", "FolderPlus", <>
  <g className="li li-plus"><path d="M12 10v6" /><path d="M9 13h6" /></g>
  <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
</>);

export const Paperclip = createIcon("paperclip", "Paperclip", <g className="li li-all">
  <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
</g>);

export const Terminal = createIcon("terminal", "Terminal", <>
  <polyline className="li li-prompt" points="4 17 10 11 4 5" />
  <path className="li li-cursor" d="M12 19h8" />
</>);

export const Bot = createIcon("bot", "Bot", <>
  <path className="li li-antenna" d="M12 8V4H8" />
  <rect width="16" height="12" x="4" y="8" rx="2" />
  <path d="M2 14h2" /><path d="M20 14h2" />
  <g className="li li-eyes"><path d="M15 13v2" /><path d="M9 13v2" /></g>
</>);

export const Wrench = createIcon("wrench", "Wrench", <g className="li li-all">
  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
</g>);

export const Settings = createIcon("settings", "Settings", <>
  <path className="li li-gear" d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
  <circle cx="12" cy="12" r="3" />
</>);

export const PanelLeft = createIcon("panel-left", "PanelLeft", <>
  <rect width="18" height="18" x="3" y="3" rx="2" />
  <path className="li li-divider" d="M9 3v18" />
</>);
export const PanelRight = createIcon("panel-right", "PanelRight", <>
  <rect width="18" height="18" x="3" y="3" rx="2" />
  <path className="li li-divider" d="M15 3v18" />
</>);
export const PanelRightOpen = createIcon("panel-right-open", "PanelRightOpen", <>
  <rect width="18" height="18" x="3" y="3" rx="2" />
  <path d="M15 3v18" />
  <path className="li li-chevron" d="m10 15-3-3 3-3" />
</>);
export const PanelRightClose = createIcon("panel-right-close", "PanelRightClose", <>
  <rect width="18" height="18" x="3" y="3" rx="2" />
  <path d="M15 3v18" />
  <path className="li li-chevron" d="m8 9 3 3-3 3" />
</>);
export const PanelsTopLeft = createIcon("panels", "PanelsTopLeft", <>
  <rect width="18" height="18" x="3" y="3" rx="2" />
  <path d="M3 9h18" />
  <path className="li li-divider" d="M9 21V9" />
</>);

export const FileDiff = createIcon("file-diff", "FileDiff", <>
  <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
  <g className="li li-plus"><path d="M9 10h6" /><path d="M12 13V7" /></g>
  <path className="li li-minus" d="M9 17h6" />
</>);

export const GitFork = createIcon("fork", "GitFork", <>
  <circle cx="6" cy="6" r="3" />
  <circle cx="18" cy="6" r="3" />
  <path d="M18 9v2c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1V9" />
  <path className="li li-stem" d="M12 12v3" />
  <circle className="li li-node" cx="12" cy="18" r="3" />
</>);

export const Link2 = createIcon("link", "Link2", <>
  <path className="li li-half-l" d="M9 17H7A5 5 0 0 1 7 7h2" />
  <path className="li li-half-r" d="M15 7h2a5 5 0 1 1 0 10h-2" />
  <path d="M8 12h8" />
</>);

export const Globe = createIcon("globe", "Globe", <>
  <circle cx="12" cy="12" r="10" />
  <path className="li li-meridian" d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" />
  <path d="M2 12h20" />
</>);

export const Activity = createIcon("activity", "Activity",
  <path className="li li-wave" pathLength={1}
    d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" />);

export const Layers = createIcon("layers", "Layers", <>
  <path className="li li-top" d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z" />
  <path d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12" />
  <path className="li li-bottom" d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17" />
</>);

/* ── Marks drawn for Loom, not lucide ─────────────────────────────────────── */

/** The sidebar toggle: a rounded triangle that points the way the sidebar will go. Filled, on its own 12x14 box. */
export const SidebarTriangle = createIcon("sidebar-triangle", "SidebarTriangle",
  <path className="li li-tri" d="M2.55 1.42c-.82-.48-1.85.11-1.85 1.06v9.04c0 .95 1.03 1.54 1.85 1.06l7.82-4.52c.82-.47.82-1.65 0-2.12L2.55 1.42Z" />);


/** Model: three stacked planes. Engaged, the outer two part around the middle one. */
export const ModelLayers = createIcon("model", "ModelLayers", <>
  <path className="li li-top" d="m12 3 9 5-9 5-9-5 9-5Z" />
  <path d="m3 12 9 5 9-5" />
  <path className="li li-bottom" d="m3 16 9 5 9-5" />
</>);

/** Expression: a face. Engaged, it blinks once. */
export const Expression = createIcon("expression", "Expression", <>
  <circle cx="11.46" cy="12.3" r="7.98" />
  <path className="li li-eyes" d="M8.22 10.38h.01M14.7 10.38h.01" strokeWidth="2.5" />
  <path className="li li-smile" d="M7.98 14.1c.9 1.44 2.1 2.18 3.48 2.18s2.58-.74 3.48-2.18" />
  <g className="li li-spark"><path d="M18.3 3.78v2.52" /><path d="M17.04 5.04h2.52" /></g>
</>);

/** Activity trace for the header: a waveform with a pulse that runs along it. */
export const Pulse = createIcon("pulse", "Pulse", <>
  <path className="li li-base" d="M2.7 12h3.84l2.16-6.72 3.9 13.38 2.64-8.64 1.86 3.66h4.2" />
  <path className="li li-runner" pathLength={1} d="M2.7 12h3.84l2.16-6.72 3.9 13.38 2.64-8.64 1.86 3.66h4.2" />
</>);
