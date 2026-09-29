from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "loom_model_bridge.py"
MANAGER = ROOT / "desktop-react" / "electron" / "modelManager.ts"
MAIN = ROOT / "desktop-react" / "electron" / "main.ts"
PRELOAD = ROOT / "desktop-react" / "electron" / "preload.cts"
CORE = ROOT / "desktop-react" / "src" / "state" / "useLoomCore.ts"
COMPOSER = ROOT / "desktop-react" / "src" / "components" / "ComposerBase.tsx"
PANEL = ROOT / "desktop-react" / "src" / "components" / "ModelPanel.tsx"
SETTINGS = ROOT / "desktop-react" / "src" / "components" / "SettingsPage.tsx"
SETTINGS_MODELS = ROOT / "desktop-react" / "src" / "components" / "ModelsSettingsPanel.tsx"
APP = ROOT / "desktop-react" / "src" / "App.tsx"


def test_minimax_catalog_is_discovered_from_provider_with_local_fallback() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert "def _fetch_minimax_model_ids(" in source
    assert '_minimax_models_url(environ)' in source
    assert 'discovered_minimax = _fetch_minimax_model_ids' in source
    assert 'minimax_model_ids = discovered_minimax or list(MINIMAX_MODEL_IDS)' in source
    assert 'minimax_source = "provider" if discovered_minimax else "fallback"' in source
    assert 'folded.startswith("minimax-")' in source


def test_relay_catalog_does_not_drop_models_that_match_official_providers() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    managed_start = source.index("def _managed_profiles(")
    managed_end = source.index("def _base_profile_for_selection", managed_start)
    managed = source[managed_start:managed_end]
    assert "or _is_minimax_model(model_id)" not in managed
    assert '"managed-relay:minimax", "MiniMax · Muxway"' in source


def test_desktop_model_catalog_has_ttl_and_async_refresh_path() -> None:
    manager = MANAGER.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")
    preload = PRELOAD.read_text(encoding="utf-8")

    assert "LOOM_MODEL_CATALOG_TTL_MS" in manager
    assert "5 * 60_000" in manager
    assert "async listSnapshot(forceRefresh = false)" in manager
    assert "this.runBridgeAsync<RegistrySnapshot>" in manager
    assert "this.catalogRefreshPromise" in manager
    assert "last known good catalog" in manager
    assert 'modelManager.listSnapshot(Boolean(forceRefresh))' in main
    assert 'listModels: (forceRefresh = false)' in preload


def test_picker_open_forces_background_refresh_and_runtime_polls_ttl() -> None:
    core = CORE.read_text(encoding="utf-8")
    composer = COMPOSER.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")

    assert "const MODEL_CATALOG_POLL_MS = 60_000;" in core
    assert "listModels<ModelSnapshot>(forceRefresh)" in core
    assert 'window.setInterval(refreshIfVisible, MODEL_CATALOG_POLL_MS)' in core
    assert "onRefreshModels?.(true)" in composer
    assert "refreshRef.current?.()" in panel


def test_removed_current_model_is_preserved_but_marked_unavailable() -> None:
    manager = MANAGER.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")

    assert 'available: false' in manager
    assert '"No longer advertised by the provider"' in manager
    assert 'selection !== currentSelection && !liveSelections.has(selection)' in manager
    assert 'profile.available === false ? "Unavailable"' in panel
    assert 'disabled={profile.available === false' in panel


def test_settings_models_page_also_refreshes_on_open() -> None:
    settings = SETTINGS.read_text(encoding="utf-8")
    settings_models = SETTINGS_MODELS.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    assert "onRefreshModels?(forceRefresh?: boolean): Promise<ModelSnapshot> | void;" in settings
    assert 'if (page !== "models" || !onRefreshModels) return;' in settings
    assert "onRefreshModels(true)" in settings
    assert "onRefreshModels={loom.refreshModels}" in app
    assert "listModels<ModelSnapshot>(true)" in settings_models


def test_thread_specific_removed_model_does_not_fall_back_to_global_current() -> None:
    core = CORE.read_text(encoding="utf-8")

    start = core.index("function modelsForThread(")
    end = core.index("function buildApprovalResponse", start) if "function buildApprovalResponse" in core[start:] else core.index("export function", start)
    block = core[start:end]
    assert "if (!profile) {" in block
    assert "available: false" in block
    assert "This model is no longer advertised by the provider" in block
    assert "profiles: [...snapshot.profiles, unavailable]" in block
