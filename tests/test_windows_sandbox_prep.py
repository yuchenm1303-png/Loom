from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

from app.agent_runtime import PermissionMode, SandboxManager, SandboxPolicy
from app.agent_runtime import sandbox as sandbox_module
from app.agent_runtime import windows_sandbox_prep as prep_module
from app.agent_runtime.windows_sandbox_prep import (
    LOOM_SANDBOX_TOOLS_CAPABILITY,
    LOOM_SANDBOX_TOOLS_CAPABILITY_SID,
    WindowsSandboxPrepStatus,
    ancestors_of,
    dacl_grants,
    locate_prep_script,
    parse_dacl_aces,
    sddl_lets_appcontainer_stat_root,
    sddl_lets_appcontainer_use_null_device,
    sddl_lets_sandbox_read,
    sddl_lets_sandbox_traverse,
)


# Descriptors read on a Windows 11 23H2 host before preparation.
NODEJS_PROTECTED = "D:P(A;OICI;0x1200a9;;;BU)(A;OICI;0x1200a9;;;AU)(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)"
GIT_INHERITED = (
    "D:AI(A;ID;FA;;;S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464)"
    "(A;ID;FA;;;SY)(A;ID;0x1200a9;;;BU)(A;OICIIOID;GRGX;;;BU)(A;ID;0x1200a9;;;AC)(A;OICIIOID;GRGX;;;AC)"
)
NULL_DEVICE_DEFAULT = "D:(A;;0x1201bf;;;WD)(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;RC)"
NULL_DEVICE_PREPARED = (
    "D:(A;;GRGWGX;;;WD)(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;RC)(A;;GRGWGX;;;AC)(A;;GRGWGX;;;S-1-15-2-2)"
)
ROOT_UNPREPARED = (
    "D:(A;;0x1000a1;;;S-1-15-3-65536-1888954469-739942743-1668119174-2468466756-4239452838-1296943325)"
    "(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;0x1200a9;;;BU)(A;OICIIO;SDGXGWGR;;;AU)(A;;LC;;;AU)"
)


def test_sddl_parser_resolves_aliases_flags_and_rights():
    aces = parse_dacl_aces("O:BAG:SYD:P(A;OICIIO;GRGX;;;AC)(D;;0x2;;;S-1-15-3-7)S:(ML;;NW;;;LW)")
    assert [(ace.kind, ace.sid) for ace in aces] == [("A", "S-1-15-2-1"), ("D", "S-1-15-3-7")]
    assert aces[0].flags == frozenset({"OI", "CI", "IO"})
    assert aces[0].applies_to_object is False
    assert aces[0].mask == 0x120089 | 0x1200A0
    assert aces[1].mask == 0x2


def test_protected_tool_folder_is_closed_until_loom_capability_is_granted():
    assert sddl_lets_sandbox_read(NODEJS_PROTECTED) is False
    granted = NODEJS_PROTECTED + f"(A;OICI;0x1200a9;;;{LOOM_SANDBOX_TOOLS_CAPABILITY_SID})"
    assert sddl_lets_sandbox_read(granted) is True


def test_inherited_all_application_packages_grant_counts_but_inherit_only_does_not():
    assert sddl_lets_sandbox_read(GIT_INHERITED) is True
    children_only = "D:(A;OICIIO;GRGX;;;AC)(A;;FA;;;SY)"
    assert sddl_lets_sandbox_read(children_only) is False


def test_deny_ace_for_the_sandbox_wins_over_allow():
    sddl = "D:(D;;0x1;;;AC)(A;OICI;0x1200a9;;;AC)"
    assert sddl_lets_sandbox_read(sddl) is False
    assert dacl_grants(sddl, ("S-1-15-2-1",), 0x80) is True  # deny does not overlap


def test_null_device_and_root_readiness_follow_the_prepared_descriptors():
    assert sddl_lets_appcontainer_use_null_device(NULL_DEVICE_DEFAULT) is False
    assert sddl_lets_appcontainer_use_null_device(NULL_DEVICE_PREPARED) is True
    assert sddl_lets_appcontainer_stat_root(ROOT_UNPREPARED) is False
    assert sddl_lets_appcontainer_stat_root(ROOT_UNPREPARED + "(A;;0x1000a1;;;AC)") is True


def test_status_names_what_is_missing_and_the_command_that_fixes_it():
    status = WindowsSandboxPrepStatus(
        null_device_ready=False,
        system_drive_ready=True,
        script=r"C:\Loom\resources\windows-sandbox-prep.ps1",
        missing_tool_dirs=(r"C:\Program Files\nodejs",),
    )
    assert status.complete is False
    assert status.missing() == ("NUL device", r"tool folders: C:\Program Files\nodejs")
    assert status.command() == (
        r'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Loom\resources\windows-sandbox-prep.ps1"'
    )
    assert WindowsSandboxPrepStatus(True, True).complete is True


def test_prep_script_is_found_beside_a_packaged_sandbox_or_in_the_checkout(tmp_path):
    packaged = tmp_path / "resources"
    packaged.mkdir()
    (packaged / "wxc-exec.exe").write_bytes(b"")
    (packaged / "windows-sandbox-prep.ps1").write_text("# packaged", encoding="utf-8")
    assert locate_prep_script(packaged / "wxc-exec.exe") == str(packaged / "windows-sandbox-prep.ps1")

    checkout = Path(locate_prep_script(None))
    assert checkout.name == "windows-sandbox-prep.ps1"
    assert checkout.parent.name == "scripts"
    script = checkout.read_text(encoding="utf-8")
    # The script and Loom must agree on the capability they grant and request.
    assert LOOM_SANDBOX_TOOLS_CAPABILITY_SID in script
    assert "Assert-MicrosoftSigned" in script


def _dacl_tier_manager(monkeypatch, readable: set[str]) -> SandboxManager:
    manager = SandboxManager(
        policy=SandboxPolicy.AUTO,
        windows_mxc_executable=r"C:\loom-test\wxc-exec.exe",
        probe_backend=False,
        system_name="Windows",
    )
    manager.windows_mxc_tier = "appcontainer-dacl"
    monkeypatch.setattr(sandbox_module, "sandbox_can_read", lambda path: str(path) in readable)
    return manager


def _config(argv) -> dict:
    assert argv[1] == "--config-base64"
    return json.loads(base64.b64decode(argv[2]).decode("utf-8"))


def test_dacl_tier_does_not_declare_folders_the_sandbox_already_reads(tmp_path, monkeypatch):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    tool_root = (tmp_path / "nodejs").resolve()
    tool_root.mkdir()
    node = tool_root / "node.exe"
    node.write_bytes(b"")
    system_root = (tmp_path / "Windows").resolve()
    system_root.mkdir()

    manager = _dacl_tier_manager(monkeypatch, readable={str(tool_root)})
    prepared = manager.prepare(
        argv=(str(node), "--version"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE,
        environment={"PATH": str(tool_root), "SYSTEMROOT": str(system_root)},
    )
    config = _config(prepared.argv)
    readonly = config["filesystem"]["readonlyPaths"]
    # MXC would try (and, for a protected folder, fail) to rewrite its DACL.
    assert str(tool_root) not in readonly
    # A folder the sandbox cannot read yet is still declared, as before.
    assert str(system_root) in readonly
    assert config["processContainer"]["capabilities"] == [LOOM_SANDBOX_TOOLS_CAPABILITY]


def test_other_tiers_keep_declaring_every_tool_folder(tmp_path, monkeypatch):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    tool_root = (tmp_path / "nodejs").resolve()
    tool_root.mkdir()
    node = tool_root / "node.exe"
    node.write_bytes(b"")

    manager = _dacl_tier_manager(monkeypatch, readable={str(tool_root)})
    manager.windows_mxc_tier = "base-container"
    prepared = manager.prepare(
        argv=(str(node), "--version"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE,
        environment={"PATH": str(tool_root)},
    )
    assert str(tool_root) in _config(prepared.argv)["filesystem"]["readonlyPaths"]


def test_probe_reason_names_the_tier():
    assert sandbox_module._probed_tier(
        "Microsoft MXC probe succeeded with appcontainer-dacl. Warnings: BaseContainer tier not selected"
    ) == "appcontainer-dacl"
    assert sandbox_module._probed_tier("Microsoft MXC probe succeeded with base-container.") == "base-container"
    assert sandbox_module._probed_tier("Microsoft MXC probe exited 1: boom") == ""


@pytest.mark.skipif(sys.platform != "win32", reason="capability SID derivation is a Windows API")
def test_capability_sid_constant_matches_windows_derivation():
    import ctypes
    from ctypes import wintypes

    kernelbase = ctypes.WinDLL("kernelbase")
    advapi32 = ctypes.WinDLL("advapi32")
    groups = ctypes.c_void_p()
    group_count = wintypes.DWORD()
    sids = ctypes.c_void_p()
    sid_count = wintypes.DWORD()
    assert kernelbase.DeriveCapabilitySidsFromName(
        LOOM_SANDBOX_TOOLS_CAPABILITY,
        ctypes.byref(groups),
        ctypes.byref(group_count),
        ctypes.byref(sids),
        ctypes.byref(sid_count),
    )
    first = ctypes.cast(sids, ctypes.POINTER(ctypes.c_void_p))[0]
    text = wintypes.LPWSTR()
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    assert advapi32.ConvertSidToStringSidW(first, ctypes.byref(text))
    assert text.value == LOOM_SANDBOX_TOOLS_CAPABILITY_SID


def _plain_windows_manager(**kwargs) -> SandboxManager:
    return SandboxManager(
        policy=SandboxPolicy.AUTO,
        windows_mxc_executable=r"C:\loom-test\wxc-exec.exe",
        probe_backend=False,
        system_name="Windows",
        **kwargs,
    )


def test_display_access_is_reported_and_follows_the_ui_switch(tmp_path, monkeypatch):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    monkeypatch.delenv("LOOM_WINDOWS_SANDBOX_UI", raising=False)

    open_manager = _plain_windows_manager()
    assert open_manager.windows_ui_access is True
    snapshot = open_manager.snapshot(workspace=workspace, permission_mode=PermissionMode.WORKSPACE)
    # The status the model and the user read must say the screen is reachable.
    assert snapshot.display_access is True
    assert snapshot.to_dict()["display_access"] is True
    config = _config(
        open_manager.prepare(
            argv=("git", "--version"),
            cwd=workspace,
            workspace=workspace,
            permission_mode=PermissionMode.WORKSPACE,
            environment={"PATH": ""},
        ).argv
    )
    assert config["ui"]["disable"] is False
    # What stays blocked even with Win32k reachable.
    assert config["ui"]["clipboard"] == "none"
    assert config["ui"]["injection"] is False
    assert config["processContainer"]["ui"]["desktopSystemControl"] is False

    locked = _plain_windows_manager(windows_ui_access=False)
    locked_snapshot = locked.snapshot(workspace=workspace, permission_mode=PermissionMode.WORKSPACE)
    assert locked_snapshot.display_access is False
    locked_config = _config(
        locked.prepare(
            argv=("git", "--version"),
            cwd=workspace,
            workspace=workspace,
            permission_mode=PermissionMode.WORKSPACE,
            environment={"PATH": ""},
        ).argv
    )
    assert locked_config["ui"]["disable"] is True


@pytest.mark.parametrize("value", ["locked", "LOCKDOWN", "off", "0", "false"])
def test_environment_can_restore_the_win32k_lockdown(monkeypatch, value):
    monkeypatch.setenv("LOOM_WINDOWS_SANDBOX_UI", value)
    assert _plain_windows_manager().windows_ui_access is False
    # An explicit argument wins over the environment.
    assert _plain_windows_manager(windows_ui_access=True).windows_ui_access is True


def test_linux_sandbox_never_reports_display_access(tmp_path):
    manager = SandboxManager(
        policy=SandboxPolicy.AUTO,
        bubblewrap_executable="/synthetic/bwrap",
        probe_backend=False,
        system_name="Linux",
    )
    snapshot = manager.snapshot(workspace=tmp_path, permission_mode=PermissionMode.WORKSPACE)
    assert snapshot.enforced is True
    assert snapshot.display_access is False


# ---- the folders above a workspace ---------------------------------------------------

# The user's profile folder and the system drive root as they read before preparation. The
# root's ALL APPLICATION PACKAGES entry is the one prepare-system-drive adds: it lets the
# sandbox stat the root and nothing more.
HOME_FOLDER = "D:PAI(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)(A;OICIID;FA;;;S-1-5-21-111-222-333-1001)"
ROOT_PREPARED = ROOT_UNPREPARED + "(A;;0x120088;;;AC)"
LIST_ENTRY = f"(A;;0x1000a1;;;{LOOM_SANDBOX_TOOLS_CAPABILITY_SID})"


@pytest.mark.parametrize(
    ("path", "above"),
    [
        (r"C:\Users\me\Loom", ("C:\\", r"C:\Users", r"C:\Users\me")),
        ("C:/Users/me/Loom/", ("C:\\", r"C:\Users", r"C:\Users\me")),
        (r"C:\Users\me\..\you\Loom", ("C:\\", r"C:\Users", r"C:\Users\you")),
        (r"c:\loom", ("c:\\",)),
        ("E:\\项目\\子", ("E:\\", "E:\\项目")),
        ("C:\\", ()),
        (r"relative\path", ()),
    ],
)
def test_ancestors_of_lists_every_folder_above_a_path_drive_root_first(path, above):
    assert ancestors_of(path) == above


def test_a_folder_is_listable_only_with_a_non_inherited_entry_for_the_sandbox():
    # Nothing in either descriptor names the sandbox; the root entry only lets it stat.
    assert sddl_lets_sandbox_traverse(HOME_FOLDER) is False
    assert sddl_lets_sandbox_traverse(ROOT_UNPREPARED) is False
    assert sddl_lets_sandbox_traverse(ROOT_PREPARED) is False
    # Loom's own capability, on that folder alone.
    assert sddl_lets_sandbox_traverse(HOME_FOLDER + LIST_ENTRY) is True
    assert sddl_lets_sandbox_traverse(ROOT_PREPARED + LIST_ENTRY) is True
    # A folder that ALL APPLICATION PACKAGES may already read and run is open as well.
    assert sddl_lets_sandbox_traverse(GIT_INHERITED) is True


@pytest.mark.parametrize(
    "entry",
    [
        # Reaches the children only, not the folder itself.
        f"(A;OICIIO;0x1000a1;;;{LOOM_SANDBOX_TOOLS_CAPABILITY_SID})",
        # Without SYNCHRONIZE CreateFile cannot open the folder (measured with (RD,X,RA)).
        f"(A;;0xa1;;;{LOOM_SANDBOX_TOOLS_CAPABILITY_SID})",
        # Stat without listing is not enough for git's GetLongPathNameW.
        f"(A;;0x100080;;;{LOOM_SANDBOX_TOOLS_CAPABILITY_SID})",
        # Stat and traverse, still no listing.
        f"(A;;0x1000a0;;;{LOOM_SANDBOX_TOOLS_CAPABILITY_SID})",
        # Listing without read-attributes: node's lstat of the folder fails.
        f"(A;;0x100021;;;{LOOM_SANDBOX_TOOLS_CAPABILITY_SID})",
        # Another capability's entry (the first one in ROOT_UNPREPARED) means nothing to Loom.
        "(A;;0x1000a1;;;S-1-15-3-65536-1-2-3-4-5-6-7)",
        # A deny wins over the grant.
        f"(D;;0x1;;;{LOOM_SANDBOX_TOOLS_CAPABILITY_SID})" + LIST_ENTRY,
    ],
)
def test_entries_that_do_not_open_the_folder_itself_to_the_sandbox_do_not_count(entry):
    assert sddl_lets_sandbox_traverse(HOME_FOLDER + entry) is False


def test_status_command_names_the_workspace_only_while_its_parents_are_closed():
    script = r"C:\Loom\windows-sandbox-prep.ps1"
    closed = WindowsSandboxPrepStatus(
        True, True, script=script, workspace=r"C:\Users\me\Loom", missing_ancestors=("C:\\", r"C:\Users"),
    )
    assert closed.complete is True  # the machine-wide parts are in place
    assert closed.workspace_ready is False
    assert closed.missing() == ("folders above the workspace: C:\\, C:\\Users",)
    assert closed.command().endswith(rf'-File "{script}" -Workspace "C:\Users\me\Loom"')
    assert closed.to_dict()["missingAncestors"] == ["C:\\", r"C:\Users"]

    opened = WindowsSandboxPrepStatus(True, True, script=script, workspace=r"C:\Users\me\Loom")
    assert opened.workspace_ready is True
    assert opened.missing() == ()
    assert "-Workspace" not in opened.command()


def test_missing_ancestors_skip_folders_whose_descriptor_cannot_be_read(monkeypatch):
    monkeypatch.setattr(prep_module, "_is_windows", lambda: True)
    answers = {"C:\\": False, r"C:\Users": None, r"C:\Users\me": True}
    monkeypatch.setattr(prep_module, "sandbox_can_traverse", lambda path: answers[str(path)])

    # Unknown is not "closed": a wrong claim would send the user to run a script for nothing.
    assert prep_module.missing_workspace_ancestors(r"C:\Users\me\Loom") == ("C:\\",)

    monkeypatch.setattr(prep_module, "_is_windows", lambda: False)
    assert prep_module.missing_workspace_ancestors(r"C:\Users\me\Loom") == ()


def test_prep_script_opens_the_folders_above_a_workspace_without_inheritance_and_undoes_it():
    script = Path(locate_prep_script(None)).read_text(encoding="utf-8")
    # Windows PowerShell 5.1 reads a script without a BOM as ANSI: keep it ASCII.
    assert script.isascii()
    # Python and the script must ask for the same rights.
    assert f"$TraverseRights = 0x{prep_module._TRAVERSE_RIGHTS:X}" in script
    install = script[script.index("function Install-Prep"):script.index("function Remove-Prep")]
    undo = script[script.index("function Remove-Prep"):script.index("\ntry {")]
    grants = [line for line in install.splitlines() if "/grant" in line and "(S,RD,RA,X)" in line]
    assert len(grants) == 1
    # The entry belongs on each folder alone: nothing below those folders opens up.
    assert "(OI)" not in grants[0] and "(CI)" not in grants[0] and "/T" not in grants[0]
    assert "$AncestorLog" in install and "$AncestorLog" in undo
    assert "/remove:g" in undo


# ---- git inside the sandbox -----------------------------------------------------------


def _child_env(manager: SandboxManager, workspace: Path) -> dict[str, str]:
    prepared = manager.prepare(
        argv=("git", "diff"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE,
        environment={
            "PATH": "",
            # The caller's own git configuration is not forwarded into the sandbox.
            "GIT_CONFIG_COUNT": "5",
            "GIT_CONFIG_KEY_0": "core.autocrlf",
            "GIT_CONFIG_VALUE_0": "true",
        },
    )
    return dict(item.split("=", 1) for item in _config(prepared.argv)["process"]["env"])


def test_git_in_the_windows_sandbox_gets_a_symlink_free_config(tmp_path, monkeypatch):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    monkeypatch.delenv("LOOM_WINDOWS_SANDBOX_GIT_SYMLINKS", raising=False)

    manager = _plain_windows_manager()
    assert manager.windows_git_compat is True
    env = _child_env(manager, workspace)
    # With core.symlinks=true the sandbox refuses git's work-tree chdir (GetFinalPathNameByHandleW),
    # so `git diff`, `grep`, `blame` and `reset --hard` died with "must be run in a work tree".
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "core.symlinks"
    assert env["GIT_CONFIG_VALUE_0"] == "false"


@pytest.mark.parametrize("value", ["keep", "KEEP", "off", "0", "false"])
def test_environment_can_keep_the_machines_own_symlink_setting(tmp_path, monkeypatch, value):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    monkeypatch.setenv("LOOM_WINDOWS_SANDBOX_GIT_SYMLINKS", value)

    manager = _plain_windows_manager()
    assert manager.windows_git_compat is False
    assert not [name for name in _child_env(manager, workspace) if name.startswith("GIT_CONFIG")]
    # An explicit argument wins over the environment.
    assert _plain_windows_manager(windows_git_compat=True).windows_git_compat is True
