from __future__ import annotations

import os
import sys

import pytest

from app.agent_runtime import PermissionMode
from app.agent_runtime.host_process_guard import host_kill_reason
from app.agent_runtime.process_runtime import ProcessStore
from app.agent_runtime.process_tools import exec_tool
from app.agent_runtime.tools import ToolContext


@pytest.mark.parametrize("argv", [
    ["cmd", "/c", "taskkill /F /IM python.exe"],
    ["taskkill", "/F", "/IM", "python.exe", "/T"],
    ["taskkill", "/F", "/IM", "pythonw.exe"],
    ["taskkill", "/F", "/IM", "Loom.exe"],
    ["taskkill", "/F", "/IM", "*"],
    ["taskkill", "/F", "/FI", "IMAGENAME eq python.exe"],
    ["powershell", "-Command", "Get-Process python | Stop-Process -Force"],
    ["powershell", "-Command", "Stop-Process -Name python* -Force"],
    ["pkill", "-f", "python"],
    ["killall", "python3"],
    ["wmic", "process", "where", "name='python.exe'", "delete"],
    ["taskkill", "/F", "/PID", str(os.getpid())],
    ["powershell", "-Command", f"Stop-Process -Id {os.getpid()}"],
    ["kill", str(os.getpid())],
    ["kill", "-TERM", str(os.getpid())],
    ["cmd", "/c", "taskkill /PID 999999 & taskkill /IM python.exe /F"],
    ["powershell", "-Command", "Stop-Process -Id 999999; Stop-Process -Name loom-host -Force"],
    ["taskkill", "/PID", "999999", "/IM", "python.exe", "/F"],
    ["cmd", "/c", "taskkill /IM python.exe /F & echo /PID 999999"],
])
def test_kills_that_reach_the_host_are_refused(argv):
    assert host_kill_reason(argv)


@pytest.mark.parametrize("argv", [
    ["taskkill", "/F", "/PID", "999999", "/T"],
    ["taskkill", "/F", "/IM", "chrome.exe"],
    ["python", "-c", "import subprocess; p.terminate()"],
    ["python", "src/train.py", "--kill-switch"],
    ["tasklist"],
    ["cmd", "/c", "tasklist | findstr /i python"],
])
def test_ordinary_commands_pass(argv):
    assert host_kill_reason(argv) is None


def test_a_script_that_kills_by_image_is_refused(tmp_path):
    script = tmp_path / "run.bat"
    script.write_text("@echo off\r\ntaskkill /F /IM python.exe\r\npython a.py\r\n", encoding="utf-8")
    assert host_kill_reason(["run.bat"], tmp_path)
    clean = tmp_path / "clean.bat"
    clean.write_text("@echo off\r\npython a.py\r\n", encoding="utf-8")
    assert host_kill_reason(["clean.bat"], tmp_path) is None


def test_exec_tool_refuses_before_starting_anything(tmp_path):
    store = ProcessStore()
    context = ToolContext("s", "t", tmp_path, permission_mode=PermissionMode.FULL_ACCESS.value,
                          services={"process_store": store})
    try:
        with pytest.raises(ValueError, match="Loom Host"):
            exec_tool().handler(context, {"argv": ["cmd", "/c", "taskkill /F /IM python.exe"]})
        assert not store.list_for_session("s")
        ok = exec_tool().handler(context, {"argv": [sys.executable, "-c", "print('hi')"]})
        assert ok.ok
    finally:
        store.terminate_all()


def test_a_sessions_default_environment_reaches_its_commands_and_an_explicit_one_wins(tmp_path):
    store = ProcessStore()
    context = ToolContext("line", "t", tmp_path, permission_mode=PermissionMode.FULL_ACCESS.value,
                          services={"process_store": store,
                                    "session_exec_env": lambda session_id: {"OMP_NUM_THREADS": "3", "MKL_NUM_THREADS": "3"}
                                    if session_id == "line" else None})
    show = [sys.executable, "-c", "import os;print(os.environ.get('OMP_NUM_THREADS'),os.environ.get('MKL_NUM_THREADS'))"]
    try:
        assert "3 3" in exec_tool().handler(context, {"argv": show}).content
        assert "5 3" in exec_tool().handler(context, {"argv": show, "env": {"OMP_NUM_THREADS": "5"}}).content
        other = ToolContext("other", "t", tmp_path, permission_mode=PermissionMode.FULL_ACCESS.value,
                            services=context.services)
        assert "3 3" not in exec_tool().handler(other, {"argv": show}).content
    finally:
        store.terminate_all()


def test_only_a_running_line_with_a_processor_share_gets_thread_limits():
    from app.project_runner.goal_lines import THREAD_VARIABLES, thread_environment
    record = {"lines": [{"agentId": "a", "cpus": 4, "state": "running"}, {"agentId": "b", "cpus": 0, "state": "running"},
                        {"agentId": "c", "cpus": 4, "state": "done"}]}
    assert thread_environment(record, "a") == {name: "4" for name in THREAD_VARIABLES}
    assert thread_environment(record, "b") == {} and thread_environment(record, "c") == {}
    assert thread_environment(record, "lead") == {} and thread_environment(None, "a") == {}


def test_the_hosts_own_image_name_and_python_scripts_are_covered(tmp_path):
    assert host_kill_reason(["taskkill", "/F", "/IM", "loom-host.exe"])
    script = tmp_path / "cleanup.py"
    script.write_text("import subprocess\nsubprocess.run('taskkill /F /IM python.exe')\n", encoding="utf-8")
    assert host_kill_reason([sys.executable, "cleanup.py"], tmp_path)
    fine = tmp_path / "train.py"
    fine.write_text("print('training')\n", encoding="utf-8")
    assert host_kill_reason([sys.executable, "train.py"], tmp_path) is None
