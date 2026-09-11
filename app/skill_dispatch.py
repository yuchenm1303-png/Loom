from __future__ import annotations

import sys
from typing import Sequence

import loom_cli

from .skill_cli import run_skill_cli


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "skill":
        return run_skill_cli(args[1:])
    return loom_cli.main(args)


__all__ = ["main"]
