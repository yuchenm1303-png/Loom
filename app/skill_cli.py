from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .agent_runtime.skill_installer import SkillInstallError, SkillInstaller


def _default_home() -> Path:
    return Path(os.environ.get("LOOM_HOME") or (Path.home() / ".loom")).expanduser().resolve()


def build_skill_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loom skill",
        description="Install and manage reusable Agent Skills bundles with inert-by-default safety boundaries.",
    )
    parser.add_argument(
        "--home",
        help="Loom runtime home; defaults to LOOM_HOME or ~/.loom",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser(
        "install",
        help="install from a local directory/ZIP or public HTTPS GitHub repository/tree URL",
    )
    install.add_argument("source")
    install.add_argument(
        "--name",
        action="append",
        default=[],
        help="install one named skill from a multi-skill source",
    )
    install.add_argument("--all", action="store_true", help="install every skill discovered in the source")
    install.add_argument(
        "--force",
        action="store_true",
        help="replace an already installed Loom-managed skill; unmanaged/manual skills are protected",
    )

    update = sub.add_parser("update", help="reinstall a Loom-managed skill from its recorded source")
    update.add_argument("name", nargs="?")
    update.add_argument("--all", action="store_true", help="update every Loom-managed skill with recorded provenance")

    remove = sub.add_parser(
        "remove",
        aliases=["uninstall"],
        help="remove a Loom-managed skill; manual/unmanaged skills are protected",
    )
    remove.add_argument("name")

    sub.add_parser("list", help="list installed user skills")

    search = sub.add_parser("search", help="search installed skills by metadata")
    search.add_argument("query", nargs="+")
    search.add_argument("--limit", type=int, default=20)

    info = sub.add_parser("info", help="show one installed skill")
    info.add_argument("name")

    return parser


def run_skill_cli(argv: Sequence[str]) -> int:
    parser = build_skill_parser()
    args = parser.parse_args(list(argv))
    home = Path(args.home).expanduser().resolve() if args.home else _default_home()
    installer = SkillInstaller(home / "skills")
    try:
        if args.command == "install":
            rows = installer.install(
                args.source,
                names=args.name,
                install_all=bool(args.all),
                force=bool(args.force),
            )
            return _emit_rows(
                rows,
                json_mode=args.json,
                empty_message="No skills installed.",
                verb="Installed",
            )

        if args.command == "update":
            if args.all:
                if args.name:
                    parser.error("update accepts either <name> or --all")
                rows = installer.update_all()
                return _emit_rows(
                    rows,
                    json_mode=args.json,
                    empty_message="No Loom-managed skills to update.",
                    verb="Updated",
                )
            if not args.name:
                parser.error("update requires <name> or --all")
            row = installer.update(args.name)
            return _emit_rows((row,), json_mode=args.json, verb="Updated")

        if args.command in {"remove", "uninstall"}:
            removed = installer.remove(args.name)
            if args.json:
                print(json.dumps({"name": args.name, "removed": removed}, ensure_ascii=False))
            elif removed:
                print(f"Removed {args.name}")
            else:
                print(f"Skill not installed: {args.name}", file=sys.stderr)
            return 0 if removed else 1

        if args.command == "list":
            return _emit_rows(
                installer.list(),
                json_mode=args.json,
                empty_message="No user skills installed.",
            )

        if args.command == "search":
            rows = installer.search(" ".join(args.query), limit=args.limit)
            return _emit_rows(
                rows,
                json_mode=args.json,
                empty_message="No installed skills matched.",
            )

        if args.command == "info":
            row = installer.get(args.name)
            return _emit_rows((row,), json_mode=args.json)

        parser.error(f"unknown skill command: {args.command}")
    except SkillInstallError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"Skill error: {exc}", file=sys.stderr)
        return 2
    return 2


def _emit_rows(
    rows,
    *,
    json_mode: bool,
    empty_message: str = "",
    verb: str = "",
) -> int:
    rows = tuple(rows)
    if json_mode:
        print(json.dumps([row.as_dict() for row in rows], ensure_ascii=False, indent=2))
        return 0
    if not rows:
        if empty_message:
            print(empty_message)
        return 0
    for row in rows:
        prefix = f"{verb} " if verb else ""
        summary = row.short_description or row.description
        source = f"  source={row.source}" if row.source else ""
        print(f"{prefix}{row.name}  {summary}{source}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_skill_cli(sys.argv[1:] if argv is None else argv)


__all__ = ["build_skill_parser", "main", "run_skill_cli"]
