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
