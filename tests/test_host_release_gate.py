"""A failed source test job must prevent publishing that exact source SHA."""
from pathlib import Path

import yaml


def test_host_release_is_explicit_and_main_only():
    root = Path(__file__).resolve().parents[1]
    release = yaml.safe_load((root / ".github/workflows/host-runtime-release.yml").read_text(encoding="utf-8"))
    ci = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    # PyYAML's YAML 1.1 loader treats the GitHub Actions key 'on' as True.
    assert set(release.get("on", release.get(True))) == {"workflow_dispatch"}
    assert release["jobs"]["test"]["if"] == "github.ref == 'refs/heads/main'"
    assert "main" in ci.get("on", ci.get(True))["push"]["branches"]


def test_host_release_depends_on_same_commit_pytest():
    root = Path(__file__).resolve().parents[1]
    release = yaml.safe_load((root / ".github/workflows/host-runtime-release.yml").read_text(encoding="utf-8"))
    ci = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = release["jobs"]
    assert "test" in jobs
    assert jobs["test"]["steps"] == ci["jobs"]["test"]["steps"]
    assert jobs["publish-host-runtime"]["needs"] == "test"
    assert jobs["publish-host-runtime"].get("if") in (None, "success()")
    checkout = jobs["test"]["steps"][0]
    assert not checkout.get("with", {}).get("ref")  # checkout defaults to this run's github.sha
