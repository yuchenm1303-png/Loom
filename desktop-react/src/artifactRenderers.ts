export type ArtifactRendererKind =
  | "web"
  | "image"
  | "pdf"
  | "video"
  | "audio"
  | "text"
  | "code"
  | "data"
  | "file";

export interface ArtifactRendererDescriptor {
  kind: ArtifactRendererKind;
  label: string;
  inline: boolean;
  side: boolean;
  interactive: boolean;
}

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "mov", "m4v"]);
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"]);
const CODE_EXTENSIONS = new Set([
  "js", "mjs", "cjs", "ts", "tsx", "jsx", "py", "go", "rs", "java", "kt",
  "css", "scss", "less", "sql", "sh", "bash", "ps1", "yaml", "yml", "toml",
]);
const TEXT_EXTENSIONS = new Set(["txt", "md", "markdown", "csv", "log"]);
const DATA_EXTENSIONS = new Set(["json", "xml"]);

export function normalizeArtifactPath(value: string): string {
  return String(value || "").trim().replaceAll("\\", "/").replace(/^\.\//, "");
}

export function artifactExtension(value: string): string {
  const clean = normalizeArtifactPath(value).split(/[?#]/, 1)[0];
  const name = clean.split("/").filter(Boolean).at(-1) || "";
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index + 1).toLowerCase() : "";
}

export function artifactName(value: string): string {
  const normalized = normalizeArtifactPath(value);
  return normalized.split("/").filter(Boolean).at(-1) || normalized || "Artifact";
}

export function artifactRenderer(value: string): ArtifactRendererDescriptor {
  const extension = artifactExtension(value);

  if (extension === "html" || extension === "htm") {
    return { kind: "web", label: "网页", inline: true, side: true, interactive: true };
  }
  if (extension === "svg") {
    return { kind: "image", label: "SVG", inline: true, side: true, interactive: false };
  }
  if (IMAGE_EXTENSIONS.has(extension)) {
    return { kind: "image", label: "图片", inline: true, side: true, interactive: false };
  }
  if (extension === "pdf") {
    return { kind: "pdf", label: "PDF", inline: true, side: true, interactive: true };
  }
  if (VIDEO_EXTENSIONS.has(extension)) {
    return { kind: "video", label: "视频", inline: true, side: true, interactive: true };
  }
  if (AUDIO_EXTENSIONS.has(extension)) {
    return { kind: "audio", label: "音频", inline: true, side: true, interactive: true };
  }
  if (DATA_EXTENSIONS.has(extension)) {
    return { kind: "data", label: "数据", inline: false, side: true, interactive: false };
  }
  if (CODE_EXTENSIONS.has(extension)) {
    return { kind: "code", label: "代码", inline: false, side: true, interactive: false };
  }
  if (TEXT_EXTENSIONS.has(extension)) {
    return { kind: "text", label: "文本", inline: false, side: true, interactive: false };
  }
  return { kind: "file", label: "文件", inline: false, side: false, interactive: false };
}

export function canRenderArtifact(value: string): boolean {
  return artifactRenderer(value).side;
}

export function canInlineRenderArtifact(value: string): boolean {
  return artifactRenderer(value).inline;
}
