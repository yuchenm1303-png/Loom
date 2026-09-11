from __future__ import annotations

import sys

from loom_cli import main as agent_main
from loom_skill_cli import main as skill_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "skill":
        return skill_main(args[1:])
    return agent_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
