from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"


def test_web_and_desktop_share_one_renderer_and_one_production_build() -> None:
    main = (SRC / "main.tsx").read_text(encoding="utf-8")
    gate = (SRC / "components" / "WebAppGate.tsx").read_text(encoding="utf-8")
    dockerfile = (ROOT / "services" / "loom_web_gateway" / "Dockerfile").read_text(encoding="utf-8")
    package = json.loads((ROOT / "desktop-react" / "package.json").read_text(encoding="utf-8"))

    assert "<WebAppGate>" in main and "<App />" in main
    assert "return children;" in gate
    assert 'import "./web-smirel.css"' not in gate
    assert package["scripts"]["build:web"] == "npm run build:renderer"
    assert "npm run build:renderer" in package["scripts"]["build"]
    assert "desktop-react/package-lock.json" in dockerfile
    assert "RUN npm ci --no-audit --no-fund" in dockerfile
    assert "RUN npm run build" in dockerfile
    assert "build:web" not in dockerfile


def test_web_only_styles_end_at_the_portal_boundary() -> None:
    forbidden: list[str] = []
    for path in SRC.rglob("*.css"):
        text = path.read_text(encoding="utf-8")
        if "data-loom-web" in text or 'data-loom-platform="web"' in text:
            forbidden.append(str(path.relative_to(ROOT)))

    portal = (SRC / "components" / "portal-base.css").read_text(encoding="utf-8")
    assert forbidden == []
    assert ".loom-portal-page" in portal
    assert "background: #21313c" in portal
    assert not (SRC / "components" / "web-smirel.css").exists()
    assert not (SRC / "web-gate.css").exists()


def test_web_build_identity_and_cache_contract_are_explicit() -> None:
    gateway = (ROOT / "services" / "loom_web_gateway" / "app.py").read_text(encoding="utf-8")
    dockerfile = (ROOT / "services" / "loom_web_gateway" / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG LOOM_BUILD_SHA=unknown" in dockerfile
    assert "LOOM_BUILD_SHA=${LOOM_BUILD_SHA}" in dockerfile
    assert '"buildSha": BUILD_SHA' in gateway
    assert '"X-Loom-Build": BUILD_SHA' in gateway
    assert '"no-store, max-age=0"' in gateway
    assert '"public, max-age=31536000, immutable"' in gateway
