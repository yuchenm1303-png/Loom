"""Loom core application package."""

from .thread_title_override import install as _install_thread_title_override

_install_thread_title_override()
del _install_thread_title_override
