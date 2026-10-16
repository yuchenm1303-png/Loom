"""All desktop, UI and runtime changes publish through the validated installer."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_installer_router_waits_for_successful_main_ci():
    workflow = yaml.safe_load((ROOT / ".github/workflows/bootstrap-update-router.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["workflow_run"]["workflows"] == ["CI"]
    job = workflow["jobs"]["route"]
    assert "conclusion == 'success'" in job["if"]
    assert "head_branch == 'main'" in job["if"]
    steps = job["steps"]
    assert "github.event.workflow_run.head_sha" in steps[0]["with"]["ref"]
    script = steps[-1]["run"]
    for source in ("src/", "electron/", "app/", "extensions/browser-current-tab/"):
        assert source in script
    assert "gh workflow run windows-release.yml" in script
    assert "git diff --quiet" in script


def test_installer_release_builds_and_checks_the_complete_package_before_publication():
    workflow = yaml.safe_load((ROOT / ".github/workflows/windows-release.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["publish-windows-x64"]["steps"]
    build = next(i for i, step in enumerate(steps) if "npm run package:win" in step.get("run", ""))
    publish = next(i for i, step in enumerate(steps) if "gh release create" in step.get("run", ""))
    assert build < publish
    package = (ROOT / "desktop-react/package.json").read_text(encoding="utf-8")
    assert "verify-packaged-renderer.mjs" in package
    assert "windows-installer-smoke.mjs" in package
