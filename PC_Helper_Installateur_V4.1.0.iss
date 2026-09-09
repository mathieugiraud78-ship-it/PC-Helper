; PC Helper — Installateur
; Version 4.1.0
; Créé par Mathieu

#define MyAppName "PC Helper"
#define MyAppVersion "4.1.0"
#define MyAppPublisher "Mathieu"
#define MyAppExeName "PC Helper.exe"

[Setup]
AppId={{8D6D2A0A-3D4D-4B8A-A2C0-PCHELPER410}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\PC Helper
DefaultGroupName=PC Helper
DisableProgramGroupPage=yes
OutputDir=installateur
OutputBaseFilename=PC_Helper_Setup_V4.1.0
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=admin

[Files]
Source: "dist\PC Helper.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\PC Helper"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\PC Helper"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer PC Helper"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
