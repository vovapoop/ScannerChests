; Chest Scanner — установщик (Inno Setup 6)
; Создаётся из build.bat; исходники — dist\ChestScanner\
; При изменении версии синхронно обновите version_info.txt и MyAppVersion.

#define MyAppName "Chest Scanner"
#define MyAppVersion "1.6.0"
#define MyAppPublisher "Chest Scanner"
#define MyAppExeName "ChestScanner.exe"
#define MyAppId "{E9CB4F3D-5A61-4B2A-9E87-1C6D8F0A3B7C}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ChestScanner
DefaultGroupName=Chest Scanner
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=ChestScannerSetup
SetupIconFile=build\icon.ico
UninstallDisplayIcon={app}\ChestScanner.exe
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
CloseApplications=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\ChestScanner\ChestScanner.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\ChestScanner\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChestScanner\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChestScanner\items.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent