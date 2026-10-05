"""A failed source test job must prevent publishing that exact source SHA."""
from pathlib import Path

import yaml


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
