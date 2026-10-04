#define MyAppName "Chest Scanner"
#define MyAppVersion "1.0"
#define MyAppPublisher "ChestScanner"

[Setup]
AppId={{A1B2C3D4-E5F6-47A8-B9C0-D1E2F3A4B5C6}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\ChestScanner
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=ChestScannerSetup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; GroupDescription: "Additional tasks:"

[Files]
Source: "main.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "launcher.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "setup_deps.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.json"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "assets\*"; DestDir: "{app}\assets"; Flags: recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: "//nologo ""{app}\launcher.vbs"""; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: "//nologo ""{app}\launcher.vbs"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{cmd}"; Parameters: "/c ""{app}\setup_deps.bat"""; WorkingDir: "{app}"; StatusMsg: "Installing dependencies..."; Flags: waituntilterminated
Filename: "{sys}\wscript.exe"; Parameters: "//nologo ""{app}\launcher.vbs"""; Description: "Launch Chest Scanner"; Flags: nowait postinstall skipifsilent