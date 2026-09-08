from __future__ import annotations

from app.agent_runtime.windows_background_process import _BackgroundSubprocessProxy


class _FakeSubprocess:
    CREATE_NO_WINDOW = 0x08000000

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def Popen(self, *args, **kwargs):  # noqa: N802 - mirrors subprocess
        self.calls.append(("Popen", args, kwargs))
        return object()

    def run(self, *args, **kwargs):
        self.calls.append(("run", args, kwargs))
        return object()


def test_windows_proxy_hides_console_children_and_preserves_existing_flags():
    fake = _FakeSubprocess()
    proxy = _BackgroundSubprocessProxy(fake, system_name="nt")

    proxy.Popen(["cmd", "/c", "echo", "ok"], creationflags=0x00000200)
    proxy.run(["taskkill", "/PID", "123"], creationflags=0x00000010)

    popen_flags = fake.calls[0][2]["creationflags"]
    run_flags = fake.calls[1][2]["creationflags"]
    assert popen_flags & fake.CREATE_NO_WINDOW
    assert popen_flags & 0x00000200
    assert run_flags & fake.CREATE_NO_WINDOW
    assert run_flags & 0x00000010


def test_non_windows_proxy_leaves_subprocess_arguments_untouched():
    fake = _FakeSubprocess()
    proxy = _BackgroundSubprocessProxy(fake, system_name="posix")

    proxy.Popen(["sh", "-c", "true"], creationflags=7)

    assert fake.calls[0][2]["creationflags"] == 7
