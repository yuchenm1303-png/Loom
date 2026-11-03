#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{8FD1A068-A1D5-4D73-AF09-5E5A7F709C4C}
AppName=Loom Remote (32-bit)
AppVersion={#AppVersion}
AppPublisher=Smirel
AppPublisherURL=https://loom.smirel.com/
DefaultDirName={localappdata}\Programs\Loom Remote x86
DefaultGroupName=Loom Remote (32-bit)
UninstallDisplayName=Loom Remote (32-bit)
OutputDir=..\artifacts\win32
OutputBaseFilename=Loom-Remote-Setup-x86
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
WizardStyle=modern
MinVersion=10.0

[Files]
Source: "bin\publish\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Loom Remote (32-bit)"; Filename: "{app}\LoomRemote.exe"
Name: "{autodesktop}\Loom Remote (32-bit)"; Filename: "{app}\LoomRemote.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Run]
Filename: "{app}\LoomRemote.exe"; Description: "打开 Loom Remote"; Flags: nowait postinstall skipifsilent
