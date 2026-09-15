from __future__ import annotations

import os
from pathlib import Path
import sys
import time
import types

from app.agent_runtime.computer_driver_runtime import ComputerDriverRuntime, _safe_driver_data
from app.agent_runtime.computer_ufo_driver import (
    UFO_COMMIT,
    UFO_TAG,
    UfoDriverConfig,
    UfoWindowsDriver,
    _normalize_base_url,
    _safe_stderr_line,
)
from app.agent_runtime.computer_single_loop_runtime import SingleLoopComputerRuntime
from app.agent_runtime.mcp_configured_runtime import ConfiguredMCPRuntime
from app.agent_runtime.ufo_sidecar import (
    _SCRATCH_PREFIX,
    _bind_ephemeral_session_logs,
    _cleanup_stale_scratch,
    _hud_point,
    _keep_raw_logs,
    _safe_parameters,
)


def test_default_runtime_no_longer_routes_through_the_ufo_driver():
    """The production MRO owns exactly one Computer Use architecture.

    This used to assert the opposite. Two architectures behind one tool name --
    the UFO HostAgent/AppAgent driver and Loom's own loop, chosen at runtime by
    whether UFO happened to be provisioned -- meant neither the model nor a
    maintainer could tell which one had executed a step. The legacy driver stays
    importable for the unit tests below it; it must not be in the default stack.
    """

    assert issubclass(ConfiguredMCPRuntime, SingleLoopComputerRuntime)
    assert not issubclass(ConfiguredMCPRuntime, ComputerDriverRuntime)


def test_ufo_pin_is_explicit_and_stable():
    assert UFO_TAG == "v3.0.8"
    assert UFO_COMMIT == "96983c73ed09e884a5f1d7ff8936c953b234b684"


def test_openai_compatible_base_url_is_normalized():
    assert _normalize_base_url("https://example.test/v1/chat/completions") == "https://example.test/v1"
    assert _normalize_base_url("https://example.test/v1/responses/") == "https://example.test/v1"


def test_ufo_status_is_side_effect_free_when_not_installed(tmp_path: Path):
    install = tmp_path / "ufo"
    sidecar = tmp_path / "ufo_sidecar.py"
    sidecar.write_text("# test sidecar\n", encoding="utf-8")
    config = UfoDriverConfig(
        install_root=install,
        source_root=install / "src",
        python=install / ".venv" / "Scripts" / "python.exe",
        sidecar=sidecar,
        api_type="openai",
        api_base="https://api.openai.com/v1",
        api_key="secret",
        api_model="vision-model",
    )
    driver = UfoWindowsDriver(config)
    status = dict(driver.status())

    assert status["sidecar_alive"] is False
    assert status["running"] is False
    assert status["source_installed"] is False
    assert status["venv_installed"] is False
    assert status["dependencies_installed"] is False
    assert status["config_installed"] is False
    assert status["preflight_ready"] is False
    if os.name == "nt":
        assert "source" in status["reason"].lower()
    else:
        assert "windows" in status["reason"].lower()
    assert status["api_key_configured"] is True
    assert "api_key" not in status


def test_ufo_child_environment_is_allowlisted(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PATH", "C:\\Windows\\System32")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-parent-provider-secret")
    monkeypatch.setenv("UNRELATED_CONNECTOR_SECRET", "must-not-cross-boundary")
    monkeypatch.setenv("LOOM_UFO_KEEP_RAW_LOGS", "1")

    config = UfoDriverConfig(
        install_root=tmp_path / "ufo",
        source_root=tmp_path / "ufo" / "src",
        python=tmp_path / "python.exe",
        sidecar=tmp_path / "ufo_sidecar.py",
        api_type="openai",
        api_base="https://example.test/v1",
        api_key="selected-ufo-provider-key",
        api_model="vision-model",
    )
    env = config.process_environment()

    assert env["PATH"] == "C:\\Windows\\System32"
    assert env["LOOM_UFO_API_KEY"] == "selected-ufo-provider-key"
    assert env["LOOM_UFO_API_MODEL"] == "vision-model"
    assert env["LOOM_UFO_KEEP_RAW_LOGS"] == "1"
    assert "OPENAI_API_KEY" not in env
    assert "UNRELATED_CONNECTOR_SECRET" not in env


def test_sidecar_redacts_text_before_driver_events():
    safe = _safe_parameters(
        "set_edit_text",
        {
            "id": "4",
            "name": "Message",
            "text": "super secret text",
            "nested": {"instruction": "another secret"},
        },
    )
    assert safe["text"] == "[TRANSIENT_TEXT]"
    assert safe["text_length"] == len("super secret text")
    assert safe["nested"]["instruction"] == "[TRANSIENT_TEXT]"
    assert safe["nested"]["instruction_length"] == len("another secret")
    assert "super secret text" not in repr(safe)
    assert "another secret" not in repr(safe)


def test_runtime_redacts_provider_errors_and_text_before_persistence():
    safe = _safe_driver_data(
        {
            "result": {"status": "failure", "error": "typed secret leaked in exception"},
            "parameters": {"text": "typed secret", "text_length": 12},
            "window": {"title": "WeChat", "rectangle": {"x": 10}},
            "stderr_tail": "provider echoed task text",
        }
    )
    assert safe["result"]["error"] == "[REDACTED_DRIVER_DATA]"
    assert safe["parameters"]["text"] == "[REDACTED_DRIVER_DATA]"
    assert safe["parameters"]["text_length"] == 12
    # stderr_tail is rewritten line by line rather than blanked, so a genuine
    # traceback keeps the frames that make a driver crash findable. Text that is
    # not traceback-shaped, like this, is still discarded.
    assert safe["stderr_tail"] == "[REDACTED_UFO_STDERR]"
    assert "provider echoed task text" not in repr(safe)
    assert safe["window"]["title"] == "WeChat"


def test_a_real_traceback_survives_the_runtime_redaction():
    """Blanking this field left a driver TypeError with no frames at all."""

    tail = "\n".join(
        (
            "Traceback (most recent call last):",
            r'  File "C:\Users\Alice\ufo\session.py", line 912, in handle',
            "TypeError: cannot use NoneType as a control label",
        )
    )
    safe = _safe_driver_data({"stderr_tail": tail})

    assert safe["stderr_tail"] == "\n".join(
        (
            "Traceback (most recent call last):",
            '  File "session.py", line 912, in handle',
            "TypeError: [REDACTED_EXCEPTION_MESSAGE]",
        )
    )
    assert "Alice" not in safe["stderr_tail"]
    assert "control label" not in safe["stderr_tail"]


def test_scrubbing_the_tail_twice_changes_nothing():
    """The runtime scrubs whatever arrives, including an already-scrubbed tail."""

    once = _safe_driver_data({"stderr_tail": "INFO: opening C:/private/report.docx"})
    twice = _safe_driver_data({"stderr_tail": once["stderr_tail"]})
    assert once == twice


def test_ufo_stderr_preserves_structure_but_drops_message_and_user_paths():
    assert _safe_stderr_line("Traceback (most recent call last):") == "Traceback (most recent call last):"
    frame = _safe_stderr_line('  File "C:\\Users\\Alice\\secret-project\\worker.py", line 42, in run_task')
    assert frame == '  File "worker.py", line 42, in run_task'
    error = _safe_stderr_line("ValueError: prompt contained super secret task text")
    assert error == "ValueError: [REDACTED_EXCEPTION_MESSAGE]"
    generic = _safe_stderr_line("provider request body: super secret task text")
    assert generic == "[REDACTED_UFO_STDERR]"
    combined = "\n".join((frame, error, generic))
    assert "Alice" not in combined
    assert "super secret" not in combined


def test_raw_ufo_logs_require_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("LOOM_UFO_KEEP_RAW_LOGS", raising=False)
    assert _keep_raw_logs() is False
    monkeypatch.setenv("LOOM_UFO_KEEP_RAW_LOGS", "1")
    assert _keep_raw_logs() is True


def test_ephemeral_ufo_logging_rebinds_context_without_ufo_install(monkeypatch, tmp_path: Path):
    from app.agent_runtime import ufo_sidecar

    monkeypatch.delenv("LOOM_UFO_KEEP_RAW_LOGS", raising=False)

    class FakeContextNames:
        LOG_PATH = "log_path"
        LOGGER = "logger"
        REQUEST_LOGGER = "request_logger"
        EVALUATION_LOGGER = "evaluation_logger"

    fake_ufo = types.ModuleType("ufo")
    fake_module = types.ModuleType("ufo.module")
    fake_context_module = types.ModuleType("ufo.module.context")
    fake_context_module.ContextNames = FakeContextNames
    fake_ufo.module = fake_module
    fake_module.context = fake_context_module
    monkeypatch.setitem(sys.modules, "ufo", fake_ufo)
    monkeypatch.setitem(sys.modules, "ufo.module", fake_module)
    monkeypatch.setitem(sys.modules, "ufo.module.context", fake_context_module)

    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()

    def fake_mkdtemp(*, prefix: str) -> str:
        target = scratch_root / f"{prefix}contract"
        target.mkdir()
        return str(target)

    monkeypatch.setattr(ufo_sidecar.tempfile, "mkdtemp", fake_mkdtemp)

    class FakeContext:
        def __init__(self) -> None:
            self.values: dict[str, object] = {}

        def set(self, key: str, value: object) -> None:
            self.values[key] = value

    class FakeSession:
        def __init__(self) -> None:
            self.log_path = "logs/original/"
            self.context = FakeContext()

    session = FakeSession()
    scratch = _bind_ephemeral_session_logs(session)

    assert scratch is not None
    assert scratch.parent == scratch_root
    assert session.log_path == str(scratch) + os.sep
    assert session.context.values[FakeContextNames.LOG_PATH] == session.log_path
    assert session.context.values[FakeContextNames.LOGGER].__class__.__name__ == "_NullWriter"
    assert session.context.values[FakeContextNames.REQUEST_LOGGER].__class__.__name__ == "_NullWriter"
    assert session.context.values[FakeContextNames.EVALUATION_LOGGER].__class__.__name__ == "_NullWriter"


def test_stale_ephemeral_ufo_scratch_is_cleaned(monkeypatch, tmp_path: Path):
    from app.agent_runtime import ufo_sidecar

    monkeypatch.setattr(ufo_sidecar.tempfile, "gettempdir", lambda: str(tmp_path))
    stale = tmp_path / f"{_SCRATCH_PREFIX}stale"
    fresh = tmp_path / f"{_SCRATCH_PREFIX}fresh"
    unrelated = tmp_path / "other-app-cache"
    stale.mkdir()
    fresh.mkdir()
    unrelated.mkdir()
    old = time.time() - (25 * 60 * 60)
    os.utime(stale, (old, old))

    _cleanup_stale_scratch()

    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_hud_point_uses_selected_application_window_not_whole_screen(monkeypatch):
    from app.agent_runtime import ufo_sidecar

    monkeypatch.setattr(
        ufo_sidecar,
        "_virtual_screen_bounds",
        lambda: {"x": 0, "y": 0, "width": 2000, "height": 1000},
    )
    point = _hud_point(
        {"rectangle": {"x": 1000, "y": 200, "width": 500, "height": 400}},
        "click_on_coordinates",
        {"x": 0.5, "y": 0.5},
    )

    assert point is not None
    assert point["screen_x"] == 1250
    assert point["screen_y"] == 400
    assert point["x_norm"] == 0.625
    assert point["y_norm"] == 0.4


class _StubResult:
    def __init__(self, status: str, error: object = None) -> None:
        self.status = status
        self.error = error


def test_a_failed_action_says_what_kind_of_failure_it_was():
    """`has_error: true` alone is not something a policy or a human can act on.

    In trace d340cb3f the model clicked a WeChat search box, tried the control
    behind it twice, and both attempts failed with nothing recorded but this
    boolean -- so the trace could not distinguish a stale control id from a Qt
    window that exposes no control tree at all.
    """

    from app.agent_runtime.ufo_sidecar import _result_status

    status = _result_status(
        _StubResult("failure", "Control with id '9' not found. Available control ids: ['1', '2']")
    )

    assert status["ok"] is False
    assert status["has_error"] is True
    assert status["error_signatures"] == ["control_id_not_found"]


def test_an_action_error_never_carries_its_message_across():
    """UFO errors quote control names, window titles and text the user typed."""

    from app.agent_runtime.ufo_sidecar import _result_status

    status = _result_status(
        _StubResult(
            "failure",
            "ValueError: could not type 'my bank passphrase hunter2' into control '搜索' "
            r"of C:\Users\Alice\wallet.txt",
        )
    )
    serialized = repr(status)

    assert status["error_type"] == "ValueError"
    assert "hunter2" not in serialized
    assert "搜索" not in serialized
    assert "Alice" not in serialized


def test_an_unrecognised_error_is_summarised_rather_than_quoted():
    from app.agent_runtime.ufo_sidecar import _result_status

    status = _result_status(_StubResult("failure", "opening C:/private/report.docx failed"))

    assert status["error_type"] == "[REDACTED_ERROR]"
    assert "report.docx" not in repr(status)
    assert status["error_length"] == len("opening C:/private/report.docx failed")


def test_a_successful_result_gains_no_error_fields():
    from app.agent_runtime.ufo_sidecar import _result_status

    status = _result_status(_StubResult("success"))

    assert status == {"status": "success", "ok": True, "has_error": False}


def test_an_observation_records_how_many_controls_it_found():
    """A window exposing no UIA tree must be distinguishable from a misread one."""

    from app.agent_runtime.ufo_sidecar import _observation_scale

    assert _observation_scale({"controls": [{"id": "1"}, {"id": "2"}]}) == {"control_count": 2}
    assert _observation_scale({"controls": []}) == {"control_count": 0}
    assert _observation_scale([1, 2, 3]) == {"control_count": 3}
    # A screenshot payload has nothing to count and must not invent a number.
    assert _observation_scale("raw-image-bytes") == {}


def test_counting_controls_never_names_them():
    from app.agent_runtime.ufo_sidecar import _observation_scale

    scale = _observation_scale(
        {"controls": [{"name": "passphrase field", "automation_id": "secret-box"}]}
    )

    assert scale == {"control_count": 1}
    assert "passphrase" not in repr(scale)
