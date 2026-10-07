!include "upgrade.nsh"
!include "processes.nsh"

!define /ifndef LOOM_PROTOCOL_SCHEME "loom"

!macro customCheckAppRunning
  !include "upgrade-entry.nsh"
  Push $R0
  Push $R1
  System::Call 'kernel32::SetEnvironmentVariable(t "LOOM_INSTALL_APP_PATH", t "$INSTDIR\${APP_EXECUTABLE_FILENAME}")'
  System::Call 'kernel32::SetEnvironmentVariable(t "LOOM_INSTALL_RESOURCE_PATH", t "$INSTDIR\resources\")'
  !insertmacro LoomFindApp $R0
  ${If} $R0 == 0
    ${IfNot} ${isUpdated}
      MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "$(appRunning)" /SD IDOK IDOK +3
      SetErrorLevel 2
      Quit
    ${EndIf}
    DetailPrint "$(appClosing)"
    !insertmacro LoomCloseApp
    Sleep 1000
    ${Do}
      !insertmacro LoomFindApp $R0
      ${If} $R0 == 1
        ${ExitDo}
      ${EndIf}
      ${If} $R0 != 0
        ${ExitDo}
      ${EndIf}
      ; Include Host children so the bundled runtime cannot keep files locked.
      !insertmacro LoomKillAppTree
      Sleep 1000
      !insertmacro LoomFindApp $R0
      ${If} $R0 != 0
        ${ExitDo}
      ${EndIf}
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(appCannotBeClosed)" /SD IDCANCEL IDRETRY +3
      SetErrorLevel 2
      Quit
    ${Loop}
  ${EndIf}
  ${If} $R0 != 0
  ${AndIf} $R0 != 1
    ; An unavailable or blocked process query is an error, not a running app.
    StrCpy $R1 "Unable to check Loom processes. Check Windows PowerShell permissions and retry.$\r$\nLog: $TEMP\${LOOM_PROTOCOL_SCHEME}-installer.log"
    ${If} $LANGUAGE == 2052
      StrCpy $R1 "无法检查 Loom 进程。请检查 Windows PowerShell 权限后重试。$\r$\n日志：$TEMP\${LOOM_PROTOCOL_SCHEME}-installer.log"
    ${EndIf}
    MessageBox MB_OK|MB_ICONSTOP "$R1" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  System::Call 'kernel32::SetEnvironmentVariable(t "LOOM_INSTALL_APP_PATH", p 0)'
  System::Call 'kernel32::SetEnvironmentVariable(t "LOOM_INSTALL_RESOURCE_PATH", p 0)'
  Pop $R1
  Pop $R0
!macroend

!macro customInstall
  WriteRegStr SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}" "" "URL:Loom Protocol"
  WriteRegStr SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}" "URL Protocol" ""
  WriteRegStr SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}\DefaultIcon" "" '"$INSTDIR\${APP_EXECUTABLE_FILENAME}",0'
  WriteRegStr SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}\shell\open\command" "" '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" "%1"'
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend

!macro customUnInstall
  Push $0
  ReadRegStr $0 SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}\shell\open\command" ""
  StrCmp $0 '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" "%1"' 0 loom_protocol_done
  DeleteRegKey SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
loom_protocol_done:
  Pop $0
!macroend
