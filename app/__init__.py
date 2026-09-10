"""Loom core application package."""

from .thread_title_override import install as _install_thread_title_override
from .thread_title_backfill import install as _install_thread_title_backfill

_install_thread_title_override()
_install_thread_title_backfill()
del _install_thread_title_override, _install_thread_title_backfill
