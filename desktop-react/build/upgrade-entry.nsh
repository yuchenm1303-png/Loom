; Expanded by customCheckAppRunning in the install section, after installUtil
; has declared its stock macro. NSIS macro definitions cannot be nested, so
; keep this preprocessor override in a separate include.
!ifndef BUILD_UNINSTALLER
  !ifndef LOOM_UPGRADE_ENTRY_DEFINED
    !define LOOM_UPGRADE_ENTRY_DEFINED
    ; The stock function is deliberately replaced and removed by NSIS.
    !pragma warning disable 6010
    !macroundef uninstallOldVersion
    !macro uninstallOldVersion ROOT_KEY
      Call LoomUpgrade_${ROOT_KEY}
    !macroend
  !endif
!endif
