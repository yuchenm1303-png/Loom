; Upgrade with the uninstaller built alongside this installer. Running a legacy
; uninstaller would also run its legacy process detection and cleanup bugs.
; The installer has already checked the app before invoking these functions.
!macro LoomUpgradeFunction ROOT_KEY
  Function LoomUpgrade_${ROOT_KEY}
    ClearErrors
    StrCpy $R0 0
    ReadRegStr $loomUninstallString ${ROOT_KEY} "${UNINSTALL_REGISTRY_KEY}" UninstallString
    ${If} $loomUninstallString == ""
      !ifdef UNINSTALL_REGISTRY_KEY_2
        ReadRegStr $loomUninstallString ${ROOT_KEY} "${UNINSTALL_REGISTRY_KEY_2}" UninstallString
      !endif
      ${If} $loomUninstallString == ""
        ClearErrors
        Return
      ${EndIf}
    ${EndIf}
    ReadRegStr $loomInstallationDir ${ROOT_KEY} "${INSTALL_REGISTRY_KEY}" InstallLocation
    ${If} $loomInstallationDir == ""
      Push $loomUninstallString
      Call GetInQuotes
      Call GetFileParent
      Pop $loomInstallationDir
    ${EndIf}
    ${If} $loomInstallationDir == ""
      ; Refuse cleanup if the registry cannot identify the old installation.
      StrCpy $R0 2
      ClearErrors
      Return
    ${EndIf}
    GetFullPathName $loomInstallationDir "$loomInstallationDir"
    ${GetRoot} "$loomInstallationDir" $R5
    ${If} $loomInstallationDir == "$R5\"
    ${OrIf} $loomInstallationDir == $WINDIR
    ${OrIf} $loomInstallationDir == $PROGRAMFILES
    ${OrIf} $loomInstallationDir == $LOCALAPPDATA
    ${OrIf} $loomInstallationDir == $APPDATA
      StrCpy $R0 2
      ClearErrors
      Return
    ${EndIf}
    ${IfNot} ${FileExists} "$loomInstallationDir\${APP_EXECUTABLE_FILENAME}"
    ${AndIfNot} ${FileExists} "$loomInstallationDir\${UNINSTALL_FILENAME}"
      ; A stale uninstall entry must not authorize deleting an arbitrary folder.
      ClearErrors
      Return
    ${EndIf}
    ${If} $installMode == "CurrentUser"
    ${OrIf} "${ROOT_KEY}" == "HKEY_CURRENT_USER"
      StrCpy $loomUninstallArgs "/currentuser"
    ${Else}
      StrCpy $loomUninstallArgs "/allusers"
    ${EndIf}
    ; Match electron-builder's shortcut and data retention policy.
    StrCpy $loomKeepShortcuts "true"
    !ifdef allowToChangeInstallationDirectory
      ${IfNot} ${isUpdated}
        StrCpy $loomKeepShortcuts "false"
      ${EndIf}
    !endif
    ${If} $loomKeepShortcuts == "true"
      ReadRegStr $R5 ${ROOT_KEY} "${INSTALL_REGISTRY_KEY}" KeepShortcuts
      ${If} $R5 == "true"
      ${AndIf} ${FileExists} "$appExe"
        StrCpy $loomUninstallArgs "$loomUninstallArgs --keep-shortcuts"
      ${EndIf}
    ${EndIf}
    ${If} ${isDeleteAppData}
      StrCpy $loomUninstallArgs "$loomUninstallArgs --delete-app-data"
    ${Else}
      StrCpy $loomUninstallArgs "$loomUninstallArgs --updated"
    ${EndIf}
    InitPluginsDir
    SetOutPath $PLUGINSDIR
    File /oname=loom-upgrade-uninstaller.exe "${UNINSTALLER_OUT_FILE}"
    DetailPrint "Removing previous Loom installation: $loomInstallationDir"
    ClearErrors
    ExecWait '"$PLUGINSDIR\loom-upgrade-uninstaller.exe" /S /KEEP_APP_DATA $loomUninstallArgs _?=$loomInstallationDir' $R0
    ; Leave failures intact for electron-builder's handleUninstallResult.
  FunctionEnd
!macroend

!macro customHeader
  !ifndef BUILD_UNINSTALLER
    Var /GLOBAL loomUninstallString
    Var /GLOBAL loomInstallationDir
    Var /GLOBAL loomUninstallArgs
    Var /GLOBAL loomKeepShortcuts
    !insertmacro LoomUpgradeFunction SHELL_CONTEXT
    !insertmacro LoomUpgradeFunction HKEY_CURRENT_USER
  !endif
!macroend
