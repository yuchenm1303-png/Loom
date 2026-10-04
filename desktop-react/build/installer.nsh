!macro customCheckAppRunning
  ; Match the application executable, not everything below $INSTDIR. During
  ; upgrades the old uninstaller also runs there and must not count as Loom.
  Push $R0
  Push $R1
  ${nsProcess::FindProcess} "${APP_EXECUTABLE_FILENAME}" $R0
  ${If} $R0 == 0
    ${IfNot} ${isUpdated}
      MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "$(appRunning)" /SD IDOK IDOK +2
      Quit
    ${EndIf}
    DetailPrint "$(appClosing)"
    ${nsProcess::CloseProcess} "${APP_EXECUTABLE_FILENAME}" $R0
    Sleep 1000
    ${Do}
      ${nsProcess::FindProcess} "${APP_EXECUTABLE_FILENAME}" $R0
      ${If} $R0 != 0
        ${ExitDo}
      ${EndIf}
      ; Include Host children so the bundled runtime cannot keep files locked.
      nsExec::Exec `"$CmdPath" /C taskkill /F /T /IM "${APP_EXECUTABLE_FILENAME}" /FI "USERNAME eq %USERNAME%"`
      Pop $R1
      Sleep 1000
      ${nsProcess::FindProcess} "${APP_EXECUTABLE_FILENAME}" $R0
      ${If} $R0 != 0
        ${ExitDo}
      ${EndIf}
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(appCannotBeClosed)" /SD IDCANCEL IDRETRY +2
      Quit
    ${Loop}
  ${EndIf}
  ${nsProcess::Unload}
  Pop $R1
  Pop $R0
!macroend

!macro customInstall
  WriteRegStr SHCTX "Software\Classes\loom" "" "URL:Loom Protocol"
  WriteRegStr SHCTX "Software\Classes\loom" "URL Protocol" ""
  WriteRegStr SHCTX "Software\Classes\loom\DefaultIcon" "" '"$INSTDIR\${APP_EXECUTABLE_FILENAME}",0'
  WriteRegStr SHCTX "Software\Classes\loom\shell\open\command" "" '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" "%1"'
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend

!macro customUnInstall
  Push $0
  ReadRegStr $0 SHCTX "Software\Classes\loom\shell\open\command" ""
  StrCmp $0 '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" "%1"' 0 loom_protocol_done
  DeleteRegKey SHCTX "Software\Classes\loom"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
loom_protocol_done:
  Pop $0
!macroend
