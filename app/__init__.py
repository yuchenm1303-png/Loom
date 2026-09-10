"""Loom core application package."""

from .model_name_compat import install as _install_model_name_compat
from .thread_title_override import install as _install_thread_title_override
from .thread_title_backfill import install as _install_thread_title_backfill
from .thread_title_rescue import install as _install_thread_title_rescue

_install_model_name_compat()
_install_thread_title_override()
_install_thread_title_backfill()
_install_thread_title_rescue()
del (
    _install_model_name_compat,
    _install_thread_title_override,
    _install_thread_title_backfill,
    _install_thread_title_rescue,
)
