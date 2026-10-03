from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "services/loom_admin/static/index.html").read_text(encoding="utf-8")
ADMIN = (ROOT / "services/loom_admin/static/admin.js").read_text(encoding="utf-8")
CSS = (ROOT / "services/loom_admin/static/admin.css").read_text(encoding="utf-8")


def test_overview_prioritizes_control_plane_and_primary_agent_metrics() -> None:
    assert "loom-admin-overview-hero" in HTML
    assert 'id="overviewHealthTitle"' in HTML
    assert 'id="kpiOnlineDevices"' in HTML
    assert 'id="kpiActiveRuns"' in HTML
    assert 'id="kpiApprovals"' in HTML
    assert 'data-admin-route="runs"' in HTML


def test_overview_moves_traffic_metrics_into_compact_24h_pulse() -> None:
    assert "loom-admin-overview-pulse" in HTML
    assert 'id="kpiTokens24"' in HTML
    assert 'id="kpiToolCalls24"' in HTML
    assert 'id="overviewRuns24"' in HTML
    assert 'id="overviewFailed24"' in HTML
    assert "loom-admin-pulse-grid" in CSS


def test_overview_operations_snapshot_uses_real_aggregate_fields() -> None:
    assert 'id="overviewDeviceCoverage"' in HTML
    assert 'id="overviewSuccessRate"' in HTML
    assert 'id="overviewWorkload"' in HTML
    assert "const successPct=runs24?" in ADMIN
    assert "a.known_devices" in ADMIN
    assert "a.failed_runs_24h" in ADMIN
    assert "overviewDeviceFill" in ADMIN


def test_overview_keeps_global_controls_compact_and_secondary() -> None:
    assert 'id="overviewFlags"' in HTML
    assert "loom-admin-quick-grid" in HTML
    assert 'data-admin-route="flags"' in HTML
    assert "loom-admin-overview-bottom" in CSS
