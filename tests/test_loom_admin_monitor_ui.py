from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "services/loom_admin/static/index.html").read_text(encoding="utf-8")
ADMIN = (ROOT / "services/loom_admin/static/admin.js").read_text(encoding="utf-8")
POLICY = (ROOT / "services/loom_admin/static/model-policy.js").read_text(encoding="utf-8")
CSS = (ROOT / "services/loom_admin/static/admin.css").read_text(encoding="utf-8")


def test_model_policy_is_owned_by_models_page_only() -> None:
    assert "section.dataset.adminPage = 'models'" in POLICY
    assert "section.hidden = true" in POLICY
    assert "function modelPageActive()" in POLICY
    assert "if (modelPageActive()) loadPolicyFresh();" in POLICY
    assert "['overview','users','devices','runs','usage','tools','sessions','models','flags','audit','system']" in ADMIN
    assert "loom-admin:pagechange" in ADMIN
    assert "#adminContent>[data-admin-page][hidden]" in CSS


def test_monitor_page_has_summary_filters_and_lazy_data() -> None:
    assert 'id="runKpiRunning"' in HTML
    assert 'id="runKpiWaiting"' in HTML
    assert 'id="runKpiFailed"' in HTML
    assert 'id="runStatusFilter"' in HTML
    assert "runStatusFilter: 'all'" in ADMIN
    assert "function runMatchesStatus" in ADMIN
    assert "request('/admin/agent-overview')" in ADMIN
    assert 'data-run-status="failed"' in HTML


def test_nav_distinguishes_monitor_usage_and_model_access() -> None:
    assert '<a href="#runs">监控</a>' in HTML
    assert '<a href="#usage">用量</a>' in HTML
    assert '<a href="#models">模型权限</a>' in HTML
