"""Loom core application package."""

from .connector_product_config import install as _install_connector_product_config
from .model_name_compat import install as _install_model_name_compat
from .runtime_capability_defaults import install as _install_runtime_capability_defaults
from .codex_mcp_discovery import install as _install_codex_mcp_discovery
from .thread_title_override import install as _install_thread_title_override
from .thread_title_backfill import install as _install_thread_title_backfill
from .thread_title_rescue import install as _install_thread_title_rescue
from .ordinary_conversations import install as _install_ordinary_conversations
from .project_git_commit import install as _install_project_git_commit
from .project_agent_files import install as _install_project_agent_files
from .connector_cross_agent_sync import install as _install_connector_cross_agent_sync
from .connector_app_server import install as _install_connector_app_server
from .agent_continuity_contract import install as _install_agent_continuity_contract
from .app_server_recovery_contract import install as _install_app_server_recovery_contract

# Release-time connector metadata must be installed before any runtime or
# connector module samples its environment. This keeps packaged builds and the
# source checkout on the same authorization path while still allowing explicit
# environment overrides in development and managed deployments.
_install_connector_product_config()
_install_model_name_compat()
_install_runtime_capability_defaults()
_install_codex_mcp_discovery()
_install_thread_title_override()
_install_thread_title_backfill()
_install_thread_title_rescue()
_install_ordinary_conversations()
_install_project_git_commit()
_install_project_agent_files()
_install_connector_cross_agent_sync()
_install_connector_app_server()
_install_agent_continuity_contract()
_install_app_server_recovery_contract()
del (
    _install_connector_product_config,
    _install_model_name_compat,
    _install_runtime_capability_defaults,
    _install_codex_mcp_discovery,
    _install_thread_title_override,
    _install_thread_title_backfill,
    _install_thread_title_rescue,
    _install_ordinary_conversations,
    _install_project_git_commit,
    _install_project_agent_files,
    _install_connector_cross_agent_sync,
    _install_connector_app_server,
    _install_agent_continuity_contract,
    _install_app_server_recovery_contract,
)
