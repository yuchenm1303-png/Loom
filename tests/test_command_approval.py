from __future__ import annotations

from app.agent_runtime.command_approval import canonicalize_command_for_approval


def test_canonicalizes_word_only_shell_scripts_to_inner_command():
    command_a = ("/bin/bash", "-lc", "cargo test -p codex-core")
    command_b = ("bash", "-lc", "cargo   test   -p codex-core")

    assert canonicalize_command_for_approval(command_a) == (
        "cargo",
        "test",
        "-p",
        "codex-core",
    )
    assert canonicalize_command_for_approval(command_a) == canonicalize_command_for_approval(
        command_b
    )


def test_canonicalizes_heredoc_scripts_to_stable_script_key():
    script = "python3 <<'PY'\nprint('hello')\nPY"
    command_a = ("/bin/zsh", "-lc", script)
    command_b = ("zsh", "-lc", script)

    assert canonicalize_command_for_approval(command_a) == (
        "__codex_shell_script__",
        "-lc",
        script,
    )
    assert canonicalize_command_for_approval(command_a) == canonicalize_command_for_approval(
        command_b
    )


def test_canonicalizes_powershell_wrappers_to_stable_script_key():
    script = "Write-Host hi"
    command_a = ("powershell.exe", "-NoProfile", "-Command", script)
    command_b = ("powershell", "-Command", script)

    assert canonicalize_command_for_approval(command_a) == (
        "__codex_powershell_script__",
        script,
    )
    assert canonicalize_command_for_approval(command_a) == canonicalize_command_for_approval(
        command_b
    )


def test_preserves_non_shell_commands():
    command = ("cargo", "fmt")
    assert canonicalize_command_for_approval(command) == command
