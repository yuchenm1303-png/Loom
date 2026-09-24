import { useEffect, useState } from "react";
import { artifactName, artifactRenderer } from "../artifactRenderers";
import "./artifact-render-surface.css";

interface ArtifactRenderSurfaceProps {
  path: string;
  workspace: string;
  compact?: boolean;
  revision?: number;
  onOpenSide?(): void;
}

export function ArtifactRenderSurface({
  path,
  workspace,
  compact = false,
  revision = 0,
  onOpenSide,
}: ArtifactRenderSurfaceProps) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const descriptor = artifactRenderer(path);
  const name = artifactName(path);

  useEffect(() => {
    let disposed = false;
    setUrl("");
    setError("");
    if (!path || !workspace || !descriptor.side) return () => { disposed = true; };

    void window.loom.localArtifactPreviewUrl(path, workspace)
      .then((next) => {
        if (!disposed) setUrl(next);
      })
      .catch((cause) => {
        if (!disposed) setError(cause instanceof Error ? cause.message : String(cause));
      });

    return () => { disposed = true; };
  }, [descriptor.side, path, revision, workspace]);

  if (!descriptor.side) return null;

  if (error) {
    return (
      <div className={`artifact-render-surface ${compact ? "is-compact" : ""} is-error`}>
        <div className="artifact-render-state">
          <strong>无法渲染 {name}</strong>
          <span>{error}</span>
        </div>
      </div>
    );
  }

  if (!url) {
    return (
      <div className={`artifact-render-surface ${compact ? "is-compact" : ""} is-loading`}>
        <div className="artifact-render-state">
          <span className="artifact-render-spinner" aria-hidden="true" />
          <strong>正在渲染 {name}</strong>
        </div>
      </div>
    );
  }

  const content = (() => {
    if (descriptor.kind === "image") {
      return <img className="artifact-render-image" src={url} alt={name} draggable={false} />;
    }
    if (descriptor.kind === "video") {
      return <video className="artifact-render-media" src={url} controls playsInline />;
    }
    if (descriptor.kind === "audio") {
      return <audio className="artifact-render-audio" src={url} controls />;
    }
    return (
      <iframe
        key={`${url}:${revision}`}
        className="artifact-render-frame"
        src={url}
        title={`预览 ${name}`}
        sandbox={descriptor.kind === "web"
          ? "allow-scripts allow-forms allow-modals allow-same-origin"
          : "allow-same-origin"}
        referrerPolicy="no-referrer"
        allow="fullscreen"
      />
    );
  })();

  return (
    <div
      className={`artifact-render-surface is-${descriptor.kind} ${compact ? "is-compact" : ""}`}
      data-renderer-kind={descriptor.kind}
    >
      {content}
      {compact && onOpenSide ? (
        <button
          type="button"
          className="artifact-inline-expand"
          onClick={onOpenSide}
          title="在右侧渲染器中展开"
        >
          <span>展开</span>
        </button>
      ) : null}
    </div>
  );
}
