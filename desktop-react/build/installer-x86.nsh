!include "LogicLib.nsh"
!include "x64.nsh"

; x86 must support the same loom:// OAuth callbacks as the x64 installer.
; On a 64-bit Windows system, preserve an existing x64 Loom association.
!define /ifndef LOOM_PROTOCOL_SCHEME "loom"

!macro customInstall
  Push $R0
  ReadRegStr $R0 SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}\shell\open\command" ""
  ${If} ${RunningX64}
    ${If} $R0 != ""
    ${AndIf} $R0 != '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" "%1"'
      DetailPrint "Keeping existing Loom protocol association on 64-bit Windows."
      Goto loom_x86_protocol_registration_done
    ${EndIf}
  ${EndIf}
  WriteRegStr SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}" "" "URL:Loom Protocol"
  WriteRegStr SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}" "URL Protocol" ""
  WriteRegStr SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}\DefaultIcon" "" '"$INSTDIR\${APP_EXECUTABLE_FILENAME}",0'
  WriteRegStr SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}\shell\open\command" "" '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" "%1"'
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
loom_x86_protocol_registration_done:
  Pop $R0
!macroend

!macro customUnInstall
  Push $R0
  ReadRegStr $R0 SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}\shell\open\command" ""
  StrCmp $R0 '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" "%1"' 0 loom_x86_protocol_uninstall_done
  DeleteRegKey SHCTX "Software\Classes\${LOOM_PROTOCOL_SCHEME}"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
loom_x86_protocol_uninstall_done:
  Pop $R0
!macroend
