; Inno Setup 6 script for the Imprint installer.
;
; Build locally:
;   iscc installers\windows\imprint.iss /DImprintVersion=0.6.4 ^
;     /DImprintSource=..\..\dist\imprint-windows-amd64 /O..\..\dist
;
; Required defines (passed with /D on the iscc command line):
;   ImprintVersion  - semver without leading 'v' (e.g. 0.6.4)
;   ImprintSource   - path to the extracted release tree (contains bin\, imprint\, requirements.txt, ...)

#ifndef ImprintVersion
  #define ImprintVersion "0.0.0-dev"
#endif
#ifndef ImprintSource
  #define ImprintSource "..\..\dist\imprint-windows-amd64"
#endif

#define AppName        "Imprint"
#define AppPublisher   "Alexandru Leca"
#define AppURL         "https://imprintmcp.alexandruleca.com"
#define AppExeName     "imprint.exe"
#define AppMutex       "ImprintLauncherSingleton"

; Pick the icon path at compile time. The CI job generates assets\imprint.ico
; from the site logo; locally that file may not exist, so fall back to the
; binary's embedded icon.
#ifexist "assets\imprint.ico"
  #define IconInstalled "{app}\assets\imprint.ico"
#else
  #define IconInstalled "{app}\bin\" + AppExeName
#endif

[Setup]
AppId={{8B6DFB4E-2C6B-4F1B-9E9D-6D6D2A7A2B71}
AppName={#AppName}
AppVersion={#ImprintVersion}
AppVerName={#AppName} {#ImprintVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/download
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputBaseFilename=imprint-windows-amd64-setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
AppMutex={#AppMutex}
UninstallDisplayName={#AppName} {#ImprintVersion}
UninstallDisplayIcon={#IconInstalled}
LicenseFile={#ImprintSource}\LICENSE
ChangesEnvironment=yes
#ifexist "assets\imprint.ico"
  SetupIconFile=assets\imprint.ico
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "addtopath";    Description: "Add Imprint to the user PATH"; GroupDescription: "Integration:"; Flags: unchecked

[Files]
; Ship the whole release tree. The launcher script + binaries + python sources
; all live under {app}. `data/` and `.venv/` are populated at first launch and
; preserved on upgrade/uninstall (see [InstallDelete] / [UninstallDelete]).
Source: "{#ImprintSource}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Our custom launcher + installer-time setup helper live alongside the binary.
Source: "imprint-launcher.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "imprint-setup.ps1";    DestDir: "{app}"; Flags: ignoreversion
; App icon. Optional - the CI build generates this from site/public/logo.svg;
; the #ifexist guard below keeps local `iscc` invocations working without it.
#ifexist "assets\imprint.ico"
Source: "assets\imprint.ico";   DestDir: "{app}\assets"; Flags: ignoreversion
#endif

[Dirs]
Name: "{app}\data";  Flags: uninsneveruninstall
Name: "{app}\.venv"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#AppName}";           Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\imprint-launcher.ps1"""; WorkingDir: "{app}"; IconFilename: "{#IconInstalled}"
Name: "{group}\Repair {#AppName}";    Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -NoExit -File ""{app}\imprint-setup.ps1"" -InstallDir ""{app}"" -Interactive"; WorkingDir: "{app}"; IconFilename: "{#IconInstalled}"; Comment: "Re-run first-run setup (visible console)"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}";     Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\imprint-launcher.ps1"""; WorkingDir: "{app}"; IconFilename: "{#IconInstalled}"; Tasks: desktopicon

[Run]
; 1. Bootstrap venv + selected-profile deps + `imprint setup` right after
;    files are copied, so the first shortcut click can skip straight to
;    "open UI". Window is VISIBLE (no /runhidden) so the user can watch uv
;    download Python + wheels and see errors without hunting for
;    first-run.log later.
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\imprint-setup.ps1"" -InstallDir ""{app}"" -Profile {code:SelectedProfile} {code:WithLlmFlag} -PauseOnFinish"; \
    WorkingDir: "{app}"; \
    StatusMsg: "Setting up Imprint (first time only - uv will download Python)..."; \
    Flags: waituntilterminated
; 2. Offer to launch Imprint on install finish (unchecked in silent mode).
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\imprint-launcher.ps1"""; \
    WorkingDir: "{app}"; \
    Description: "Launch {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[Registry]
; Adds {app}\bin to the user PATH when the "addtopath" task is selected.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}\bin"; Tasks: addtopath; Check: NeedsPathEntry('{app}\bin')

[UninstallDelete]
; Only remove files we installed; data/ and .venv/ are preserved via [Dirs] flag.
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\.first-run.done"

[Code]
#include "ProfilePage.iss"

procedure InitializeWizard();
begin
  CreateProfilePages();
end;

function NeedsPathEntry(const Dir: string): Boolean;
var
  ExistingPath: string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', ExistingPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Lowercase(Dir) + ';', ';' + Lowercase(ExistingPath) + ';') = 0;
end;
