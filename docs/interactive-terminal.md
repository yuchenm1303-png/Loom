# Project terminals

Open **Terminal** in the right tool dock, or press **Ctrl+`**. The shortcut can
be changed in Settings → Keyboard shortcuts.

Choose an installed Shell and a directory relative to the project, then select
**New terminal**. Each tab owns a real managed PTY. Shell history, completion,
ANSI colors, interactive programs, and Ctrl+C are handled by the shell and the
terminal emulator. Ctrl+Shift+C copies the selection; the normal paste shortcut
pastes into the terminal. Ctrl+F searches output.

The panel supports multiple sessions, tab switching with arrow keys, renaming,
clear screen, export, font size, resizing and maximize/restore. Hiding the panel
or switching conversations keeps processes alive. Closing a tab terminates its
process tree. Exited tabs retain their output and offer rerun. Sessions survive
renderer reloads while the same Loom host stays alive; they do not survive a
host or application restart. Interactive shells have no command timeout; code
block commands retain the existing one-hour execution limit.

Shell, PowerShell, Command Prompt, Python and JavaScript code blocks have **Run**
and **Terminal** controls. Run shows bounded inline output and stdin input;
Terminal opens the same process if already started, or starts it in a terminal
tab. Code is never executed automatically, during streaming, or for unsupported
languages such as SQL. Missing interpreters produce an explicit error. Commands
run in the project root; a new interactive session can start in a project
subdirectory. Terminal output is not added to the model conversation by default.

## Host API

- `terminal/profiles`: installed profiles (id and label).
- `terminal/session/create`: workspace, optional shell, cwd, rows, cols; optional
  command/language creates a command tab instead of an interactive shell.
- `terminal/session/list`: sessions owned by this workspace.
- `terminal/session/read`: processId, workspace, independent absolute cursor;
  returns raw output, next cursor, truncation/reset flags, and process status.
- `terminal/session/write`: raw input, including terminal control sequences.
- `terminal/session/resize`: rows and cols, without draining agent output.
- `terminal/session/rename`: title, limited to 80 characters.
- `terminal/session/close`: terminate the process tree and remove the tab.

Sessions use the existing thread ownership, permission mode, sandbox and
sanitized environment. The initial cwd must resolve inside the workspace.
Independent output cursors let inline cards, the terminal emulator and agent
process monitoring coexist. The host retains a bounded 100,000-character tail;
the emulator retains up to 10,000 scrollback lines. When a reader falls behind
the host tail, it receives a truncation flag instead of silently missing output.

`tests/test_interactive_terminal.py` exercises real PTYs, ownership, directories,
resizing, command completion and output rollover. The browser integration test
uses an isolated temporary host and real PowerShell/ConPTY on Windows:

```powershell
$env:LOOM_TEST_PYTHON = 'C:/path/to/python.exe'
$env:LOOM_TEST_ORIGIN = 'http://127.0.0.1:5173'
# Set LOOM_PLAYWRIGHT_MODULE and LOOM_CHROMIUM_PATH when not installed locally.
node desktop-react/scripts/tests/terminal-panel-browser.mjs
```
