; Match the app's full path plus bundled runtime processes below resources\.
; This also cleans up orphan Hosts/browsers left behind by a crashed app.
; A same-named app elsewhere, setup and uninstall executables are excluded.
; Pass paths through the environment so spaces and apostrophes remain data.
!define LOOM_APP_QUERY `Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { if (-not $$_.ExecutablePath -and $$_.Name -eq [IO.Path]::GetFileName($$env:LOOM_INSTALL_APP_PATH)) { throw ('Cannot inspect executable path for process ' + $$_.ProcessId) }; $$_.ExecutablePath -and ([string]::Equals($$_.ExecutablePath, $$env:LOOM_INSTALL_APP_PATH, [StringComparison]::OrdinalIgnoreCase) -or $$_.ExecutablePath.StartsWith($$env:LOOM_INSTALL_RESOURCE_PATH, [StringComparison]::OrdinalIgnoreCase)) }`

!macro LoomProcessLog MODE RESULT
  Push $R2
  ClearErrors
  FileOpen $R2 "$TEMP\${LOOM_PROTOCOL_SCHEME}-installer.log" a
  ${IfNot} ${Errors}
    FileWrite $R2 "${MODE}: ${RESULT} | $INSTDIR$\r$\n$R1$\r$\n"
    FileClose $R2
  ${EndIf}
  Pop $R2
  ClearErrors
!macroend

!macro LoomFindApp RESULT
  nsExec::ExecToStack `"$PowerShellPath" -NoProfile -NonInteractive -Command "try { $$p = @(${LOOM_APP_QUERY}); if ($$p.Count) { exit 0 }; exit 1 } catch { Write-Output $$_.Exception.Message; exit 2 }"`
  Pop ${RESULT}
  Pop $R1
  !insertmacro LoomProcessLog "check" ${RESULT}
!macroend

!macro LoomCloseApp
  nsExec::ExecToStack `"$PowerShellPath" -NoProfile -NonInteractive -Command "try { ${LOOM_APP_QUERY} | ForEach-Object { [Diagnostics.Process]::GetProcessById($$_.ProcessId).CloseMainWindow() | Out-Null }; exit 0 } catch { Write-Output $$_.Exception.Message; exit 2 }"`
  Pop $R0
  Pop $R1
  !insertmacro LoomProcessLog "close" $R0
!macroend

!macro LoomKillAppTree
  nsExec::ExecToStack `"$PowerShellPath" -NoProfile -NonInteractive -Command "try { ${LOOM_APP_QUERY} | ForEach-Object { & ($$env:SystemRoot + '\System32\taskkill.exe') /F /T /PID $$_.ProcessId }; exit 0 } catch { Write-Output $$_.Exception.Message; exit 2 }"`
  Pop $R0
  Pop $R1
  !insertmacro LoomProcessLog "kill tree" $R0
!macroend
