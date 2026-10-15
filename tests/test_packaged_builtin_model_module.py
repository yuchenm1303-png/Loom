"""The installed CLI entry points must ship every root-level module they import."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_builtin_model_bridge_dependency_is_packaged():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    modules = set(config["tool"]["setuptools"]["py-modules"])
    assert "loom_model_bridge" in modules
    assert "loom_builtin_models" in modules
    packages = set(config["tool"]["setuptools"]["packages"]["find"]["include"])
    assert "services*" in packages
    assert (ROOT / "services/loom_model_policy/catalog.py").is_file()
    assert (ROOT / "services/loom_model_gateway/routes.py").is_file()
    assert (ROOT / "loom_builtin_models.py").is_file()
