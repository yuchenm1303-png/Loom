import subprocess
import sys


def test_packaged_code_command_does_not_import_unrelated_entrypoints():
    result = subprocess.run(
        [sys.executable, "loom_desktop_runtime.py", "-c",
         "import sys; assert not any(name in sys.modules for name in "
         "('loom_app_server', 'loom_chatgpt_mcp', 'loom_model_admin', 'loom_model_bridge')); print('isolated')"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"
