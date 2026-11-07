"""scripts/deploy-loom-web-main.sh runs against production from GitHub main on a timer, so
its decisions are exercised here on a real git repository with a recording `docker`."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy-loom-web-main.sh"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs a POSIX shell")


def _shell(path: Path) -> str:
    """A path as the script's shell spells it: Git Bash tools read C:/ as a remote host."""
    text = str(path)
    if os.name == "nt":
        text = f"/{text[0].lower()}{text[2:]}".replace("\\", "/")
    return text

DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_LOG"
case "$1 $2" in
  "inspect loom-account") cat "$STUB_ACCOUNT_SOURCE" ;;
  "inspect -f") echo healthy ;;
  "image inspect") echo old-image ;;
  "create "*) echo cid ;;
  "cp cid:/srv/loom-web/static/.")
    mkdir -p "$3/assets"; echo "<html>$STUB_SHA</html>" >"$3/index.html"; echo css >"$3/assets/new-$STUB_SHA.css" ;;
  "exec loom-web")
    if [[ "$*" == *healthz* ]]; then
      [[ -n "${STUB_SERVED:-}" ]] && echo "$STUB_SERVED" || cat "$STUB_STATIC/build-sha.txt"
    else
      echo "$STUB_SHA"
    fi ;;
esac
exit 0
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


class Stage:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.origin = tmp / "origin"
        self.src = tmp / "src"
        self.deploy = tmp / "deploy"
        self.static = tmp / "static"
        self.log = tmp / "docker.log"
        self.account_source = tmp / "account-source"
        self.bin = tmp / "bin"
        for folder in (self.origin, self.deploy, self.bin):
            folder.mkdir()
        self._stub("docker", DOCKER)
        self._stub("flock", "#!/usr/bin/env bash\nexit 0\n")
        _git(self.origin, "init", "-q", "-b", "main")
        _git(self.origin, "config", "user.email", "t@example.com")
        _git(self.origin, "config", "user.name", "t")
        for name in ("services/__init__.py", "services/loom_account/server.py", "services/loom_web_gateway/app.py",
                     "desktop-react/src/App.tsx", "desktop-react/src/mobile/main.tsx", "desktop-react/electron/main.ts", "docs/a.md"):
            self.write(name, "one\n")
        self.commit()
        _git(self.tmp, "clone", "-q", str(self.origin), str(self.src))
        _git(self.src, "config", "user.email", "t@example.com")
        _git(self.src, "config", "user.name", "t")

    def _stub(self, name: str, body: str) -> None:
        target = self.bin / name
        target.write_text(body, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IEXEC)

    def write(self, name: str, text: str) -> None:
        target = self.origin / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self) -> str:
        _git(self.origin, "add", "-A")
        _git(self.origin, "commit", "-q", "-m", "change")
        return _git(self.origin, "rev-parse", "HEAD")

    def change(self, name: str) -> str:
        self.write(name, f"{name} changed {_git(self.origin, 'rev-parse', 'HEAD')}\n")
        return self.commit()

    def run(self, sha: str, served: str = "") -> subprocess.CompletedProcess[str]:
        self.log.write_text("", encoding="utf-8")
        # The account source hash is taken after the reset, so it always reads as unchanged here.
        subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=self.src, check=True)
        tree = subprocess.run(
            [BASH, "-c", "git ls-tree origin/main -- services/__init__.py services/loom_account | sha256sum | cut -d ' ' -f1"],
            cwd=self.src, check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.account_source.write_text(tree + "\n", encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "LOOM_WEB_SOURCE_DIR": _shell(self.src),
            "LOOM_WEB_DEPLOY_DIR": _shell(self.deploy),
            "LOOM_WEB_STATIC_HOST_DIR": _shell(self.static),
            "LOOM_WEB_LOCK_FILE": _shell(self.tmp / "lock"),
            "LOOM_EDGE_CADDYFILE": _shell(self.tmp / "no-caddyfile"),
            "STUB_LOG": _shell(self.log),
            "STUB_ACCOUNT_SOURCE": _shell(self.account_source),
            "STUB_STATIC": _shell(self.static),
            "STUB_SHA": sha,
            "STUB_SERVED": served,
        }
        return subprocess.run([BASH, _shell(SCRIPT)], env=env, capture_output=True, text=True, encoding="utf-8")

    @property
    def commands(self) -> str:
        return self.log.read_text(encoding="utf-8")

    @property
    def marker(self) -> str:
        return (self.deploy / "deployed-main").read_text(encoding="utf-8").strip()


@pytest.fixture
def stage(tmp_path) -> Stage:
    stage = Stage(tmp_path)
    first = _git(stage.origin, "rev-parse", "HEAD")
    outcome = stage.run(first)
    assert outcome.returncode == 0, outcome.stderr
    return stage


def recreates(stage: Stage) -> bool:
    return "compose" in stage.commands and "--force-recreate" in stage.commands


def test_the_first_run_recreates_once_and_mounts_the_bundle(stage) -> None:
    assert recreates(stage)
    assert "compose -f compose.prod.yml -f" in stage.commands
    override = (stage.deploy / "compose.static.yml").read_text(encoding="utf-8")
    assert "LOOM_WEB_STATIC_DIR: /srv/loom-web/live-static" in override
    assert f"{_shell(stage.static)}:/srv/loom-web/live-static:ro" in override
    assert (stage.static / "index.html").is_file()


@pytest.mark.parametrize(
    "name",
    ["docs/a.md", "desktop-react/electron/main.ts", "desktop-react/src/mobile/main.tsx"],
)
def test_a_change_outside_the_web_app_touches_nothing_running(stage, name) -> None:
    sha = stage.change(name)
    outcome = stage.run(sha)
    assert outcome.returncode == 0, outcome.stderr
    assert not recreates(stage)
    assert "build" not in stage.commands
    assert stage.marker == sha


def test_a_ui_change_replaces_the_bundle_without_a_restart(stage) -> None:
    before = (stage.static / "index.html").read_text(encoding="utf-8")
    old_assets = sorted(path.name for path in (stage.static / "assets").iterdir())
    sha = stage.change("desktop-react/src/App.tsx")
    outcome = stage.run(sha)
    assert outcome.returncode == 0, outcome.stderr
    assert not recreates(stage)
    assert "build" in stage.commands
    assert (stage.static / "index.html").read_text(encoding="utf-8") != before
    assert (stage.static / "build-sha.txt").read_text(encoding="utf-8").strip() == sha
    # Pages still open keep the hashed files they were built against.
    assert set(old_assets) <= {path.name for path in (stage.static / "assets").iterdir()}
    assert stage.marker == sha


def test_a_gateway_change_restarts_it(stage) -> None:
    sha = stage.change("services/loom_web_gateway/app.py")
    outcome = stage.run(sha)
    assert outcome.returncode == 0, outcome.stderr
    assert recreates(stage)
    assert stage.marker == sha


def test_a_bundle_the_gateway_does_not_serve_rolls_back(stage) -> None:
    deployed = stage.marker
    before = (stage.static / "index.html").read_text(encoding="utf-8")
    sha = stage.change("desktop-react/src/App.tsx")
    outcome = stage.run(sha, served="something-else")
    assert outcome.returncode == 13
    assert (stage.static / "index.html").read_text(encoding="utf-8") == before
    assert (stage.static / "build-sha.txt").read_text(encoding="utf-8").strip() == deployed
    assert stage.marker == deployed


def test_an_unknown_previous_commit_takes_the_full_path(stage) -> None:
    (stage.deploy / "deployed-main").write_text("0" * 40 + "\n", encoding="utf-8")
    sha = stage.change("docs/a.md")
    outcome = stage.run(sha)
    assert outcome.returncode == 0, outcome.stderr
    assert recreates(stage)
