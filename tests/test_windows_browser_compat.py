from pathlib import Path

from app.agent_runtime.windows_browser_compat import installed_browser_channel


def test_windows_ia32_prefers_installed_edge_over_chrome():
    paths = {
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Users/Test/AppData/Local/Google/Chrome/Application/chrome.exe"),
    }
    channel = installed_browser_channel(
        system_name="Windows", bits=32,
        environ={"ProgramFiles": "C:/Program Files", "LOCALAPPDATA": "C:/Users/Test/AppData/Local"},
        file_exists=lambda path: path in paths,
    )
    assert channel == "msedge"


def test_windows_ia32_accepts_installed_chrome_if_edge_missing():
    channel = installed_browser_channel(
        system_name="Windows", bits=32,
        environ={"PROGRAMFILES": "C:/Apps"},
        file_exists=lambda path: path == Path("C:/Apps/Google/Chrome/Application/chrome.exe"),
    )
    assert channel == "chrome"


def test_x64_and_non_windows_keep_existing_browser_channel_defaults():
    for system, bits in (("Windows", 64), ("Linux", 32), ("Darwin", 64)):
        assert installed_browser_channel(
            system_name=system, bits=bits,
            environ={"PROGRAMFILES": "C:/Apps"},
            file_exists=lambda _: True,
        ) == ""


def test_unavailable_browser_is_not_falsely_reported_as_installed():
    assert installed_browser_channel(
        system_name="Windows", bits=32,
        environ={"PROGRAMFILES": "C:/Apps"},
        file_exists=lambda _: False,
    ) == ""
