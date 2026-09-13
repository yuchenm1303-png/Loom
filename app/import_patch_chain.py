"""Composable spec lookup for Loom's meta_path patch modules.

Several modules under ``app/`` patch a runtime class by installing a
``MetaPathFinder`` that wraps the target module's loader and runs a ``patch()``
after ``exec_module``. Each one used to resolve the spec with

    sys.meta_path.remove(self)
    spec = importlib.machinery.PathFinder.find_spec(fullname, path)

which reaches ``PathFinder`` directly and therefore skips every other finder.
That is fine while one module owns a target, but ``app.app_server_project_move``
has two patchers -- ``project_agent_files`` and ``project_git_commit``. Python
stops at the first finder returning a spec, so whichever installed last sat at
``sys.meta_path[0]``, won, and silently dropped the other module's patch.
``project_git_commit`` lost, and its ``project_git_stage`` / ``gitStage``
endpoints never reached the service class.

Delegating to the *remaining* finders instead lets the wrappers nest: the outer
finder wraps a loader that is already wrapped, and both patches run.
"""
from __future__ import annotations

import importlib.machinery
import sys
from types import ModuleType
from typing import Any


def find_spec_without(
    finder: Any,
    fullname: str,
    path: Any,
    target: ModuleType | None = None,
) -> importlib.machinery.ModuleSpec | None:
    """Resolve ``fullname`` using every finder except ``finder`` itself.

    Temporarily drops ``finder`` from ``sys.meta_path`` so a peer patcher for
    the same module still gets its turn, then falls back to ``PathFinder`` when
    no other finder claims the module. ``finder`` is restored at its original
    position so install order -- and therefore patch nesting order -- stays
    stable across repeated imports.
    """

    try:
        index = sys.meta_path.index(finder)
    except ValueError:
        index = 0
        removed = False
    else:
        del sys.meta_path[index]
        removed = True

    try:
        for peer in list(sys.meta_path):
            peer_find_spec = getattr(peer, "find_spec", None)
            if not callable(peer_find_spec):
                continue
            try:
                spec = peer_find_spec(fullname, path, target)
            except Exception:
                # A peer finder that cannot answer must not turn a normal
                # import into an ImportError raised from inside our patcher.
                continue
            if spec is not None:
                return spec
        return importlib.machinery.PathFinder.find_spec(fullname, path)
    finally:
        if removed:
            sys.meta_path.insert(index, finder)


__all__ = ["find_spec_without"]
