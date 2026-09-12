"""Loom core application package."""

from .model_name_compat import install as _install_model_name_compat
from .runtime_capability_defaults import install as _install_runtime_capability_defaults
from .thread_title_override import install as _install_thread_title_override
from .thread_title_backfill import install as _install_thread_title_backfill
from .thread_title_rescue import install as _install_thread_title_rescue
from .ordinary_conversations import install as _install_ordinary_conversations
from .project_git_commit import install as _install_project_git_commit
from .project_agent_files import install as _install_project_agent_files

_install_model_name_compat()
_install_runtime_capability_defaults()
_install_thread_title_override()
_install_thread_title_backfill()
_install_thread_title_rescue()
_install_ordinary_conversations()
_install_project_git_commit()
_install_project_agent_files()
del (
    _install_model_name_compat,
    _install_runtime_capability_defaults,
    _install_thread_title_override,
    _install_thread_title_backfill,
    _install_thread_title_rescue,
    _install_ordinary_conversations,
    _install_project_git_commit,
    _install_project_agent_files,
)
