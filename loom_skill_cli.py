from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.agent_runtime.skill_installer import (
    SkillInstallError,
    SkillInstaller,
    default_skill_install_root,
)


def _installer_from_args(args: argparse.Namespace) -> SkillInstaller:
    if args.root:
        root = Path(args.root).expanduser().resolve(strict=False)
    else:
        root = default_skill_install_root(args.home)
    return SkillInstaller(root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loom skill",
        description="Install and manage inert Agent Skills bundles safely.",
    )
    parser.add_argument("--home", help="Loom home; defaults to LOOM_HOME or ~/.loom")
    parser.add_argument("--root", help="override the managed skill root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="install from a local directory/ZIP or public GitHub URL")
    install.add_argument("source")
    install.add_argument("--force", action="store_true", help="replace an existing Loom-managed skill")

    subparsers.add_parser("list", help="list skills in the managed user root")

    update = subparsers.add_parser("update", help="reinstall a managed skill from its recorded source")
    update.add_argument("name")

    remove = subparsers.add_parser("remove", aliases=["uninstall"], help="remove a Loom-managed skill")
    remove.add_argument("name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    installer = _installer_from_args(args)
    try:
        if args.command == "install":
            result = installer.install(args.source, force=args.force)
            action = "Updated" if result.replaced else "Installed"
            print(f"{action} {result.name} -> {result.path}")
            print(f"Source: {result.source}")
            print(f"Files: {result.file_count}, bytes: {result.total_bytes}, sha256: {result.content_sha256}")
            print("Install policy: inert; no bundled script or executable was run.")
            return 0
        if args.command == "list":
            records = installer.list_installed()
            if not records:
                print(f"No skills found in {installer.root}")
                return 0
            for record in records:
                state = "managed" if record.managed else "manual"
                print(f"{record.name:28} {state:8} {record.source}")
            return 0
        if args.command == "update":
            result = installer.update(args.name)
            print(f"Updated {result.name} -> {result.path}")
            print(f"sha256: {result.content_sha256}")
            return 0
        if args.command in {"remove", "uninstall"}:
            removed = installer.remove(args.name)
            print(f"Removed {args.name} from {removed}")
            return 0
    except (OSError, SkillInstallError) as exc:
        print(f"Skill operation failed: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
