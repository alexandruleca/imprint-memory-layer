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
; preserved on upgrade; uninstall removes them unless data is kept (ADR 0028).
Source: "{#ImprintSource}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Our custom launcher + installer-time setup helper live alongside the binary.
Source: "imprint-launcher.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "imprint-uninstall.ps1"; DestDir: "{app}"; Flags: ignoreversion
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
; Silent installs skip custom wizard pages. Provision explicitly in that case:
; deterministic CPU/no-LLM defaults on a first install (ADR 0012), the
; persisted profile on an update (ADR 0023).
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -NonInteractive -File ""{app}\imprint-setup.ps1"" -InstallDir ""{app}"" {code:SilentProfileArgs}"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Check: WizardSilent
; `imprint update` runs this installer with /RELAUNCH=1 and has closed
; Imprint; reopen it once the update is provisioned.
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\imprint-launcher.ps1"""; \
    WorkingDir: "{app}"; \
    Flags: nowait runhidden; Check: WizardSilent and RelaunchRequested
; Bootstrap now runs on the inline "Setting up Imprint" wizard page (SetupOutputPage.iss)
; so users see live output without a separate PowerShell window.
; Offer to launch Imprint on install finish (unchecked in silent mode).
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
; `imprint uninstall` removes data/ and .venv/ unless data is kept; see [Code].
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\.first-run.done"
Type: files; Name: "{app}\.setup-result"
Type: files; Name: "{app}\.wsl-integrations.txt"
Type: files; Name: "{app}\.wsl-registrations.json"
Type: files; Name: "{app}\uninstall-cleanup.log"

[Code]
#include "ProfilePage.iss"
#include "WSLPage.iss"
#include "SetupOutputPage.iss"

procedure InitializeWizard();
begin
  // Silent provisioning belongs exclusively to [Run]. Hidden custom pages
  // cannot wait for Next, and must not disable Inno's automatic navigation.
  if WizardSilent then Exit;
  CreateProfilePages();
  CreateWSLPage();
  CreateSetupOutputPage();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (not WizardSilent) and (GWslPage <> nil) and (CurPageID = GWslPage.ID) then
    DiscoverWSLProfiles();
  if (not WizardSilent) and (GOutputPage <> nil) and (CurPageID = GOutputPage.ID) then
    StartBootstrapOnPage();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (not WizardSilent) and (GWslPage <> nil) and (CurPageID = GWslPage.ID) then
    Result := SaveWSLSelections();
end;

function BackButtonClick(CurPageID: Integer): Boolean;
begin
  // Prevent going back while bootstrap is running.
  Result := not ((GOutputPage <> nil) and (CurPageID = GOutputPage.ID) and not GBootDone);
end;

function SilentProfileArgs(Param: string): string;
begin
  if FileExists(ExpandConstant('{app}\data\profile.json')) then
    Result := ''
  else
    Result := '-Profile cpu -NoLlm';
end;

function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
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

var
  KeepData: Boolean;

function HasSwitch(const Name: string): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

// Cleanup needs the CLI; refuse before Inno's confirmation if it is gone.
// The cleanup itself waits until the user has confirmed (usUninstall).
function InitializeUninstall(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\bin\imprint.exe'));
  if not Result then
  begin
    Log('bin\imprint.exe is missing; uninstall cancelled.');
    if not UninstallSilent then
      MsgBox('Imprint is incomplete: ' + ExpandConstant('{app}\bin\imprint.exe') + ' is missing.' + #13#10 +
        'Run the Imprint installer to repair it, then uninstall.', mbError, MB_OK);
  end;
end;

// Remove only this installation's PATH segment, preserving all other values.
function RemovePathEntry(): Boolean;
var
  Separator: Integer;
  ExistingPath, Remaining, Part, UpdatedPath, BinDir: string;
begin
  Result := True;
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', ExistingPath) then Exit;
  Remaining := ExistingPath;
  UpdatedPath := '';
  BinDir := Lowercase(RemoveBackslashUnlessRoot(ExpandConstant('{app}\bin')));
  while Remaining <> '' do
  begin
    Separator := Pos(';', Remaining);
    if Separator = 0 then begin Part := Remaining; Remaining := ''; end
    else begin Part := Copy(Remaining, 1, Separator - 1); Delete(Remaining, 1, Separator); end;
    if Lowercase(RemoveBackslashUnlessRoot(RemoveQuotes(Trim(Part)))) <> BinDir then
    begin
      UpdatedPath := UpdatedPath + Part;
      if Separator <> 0 then UpdatedPath := UpdatedPath + ';';
    end;
  end;
  if UpdatedPath <> ExistingPath then
    Result := RegWriteExpandStringValue(HKCU, 'Environment', 'Path', UpdatedPath);
end;

// After confirmation and before any file is removed: disconnect clients,
// stop every Imprint process and remove the runtime and, unless kept, the
// data (ADR 0028). Any failure aborts with the installation intact.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Params: string;
begin
  case CurUninstallStep of
    usUninstall:
      begin
        KeepData := HasSwitch('/KEEPDATA');
        if not UninstallSilent then
          case MsgBox('Also delete your Imprint memories, workspaces and settings?' + #13#10#13#10 +
              'Yes deletes everything. No keeps them in ' + ExpandConstant('{app}\data') + ' for a later reinstall.',
              mbConfirmation, MB_YESNOCANCEL) of
            IDNO: KeepData := True;
            IDCANCEL: Abort;
          end;
        Params := '-NoProfile -ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -File "' +
          ExpandConstant('{app}\imprint-uninstall.ps1') + '" -InstallDir "' + ExpandConstant('{app}') + '"';
        if KeepData then Params := Params + ' -KeepData';
        if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params,
          ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then Code := -1;
        if Code <> 0 then
        begin
          Log('Imprint cleanup failed; uninstall cancelled.');
          if not UninstallSilent then
            MsgBox('Imprint could not finish cleaning up. No program files were removed.' + #13#10 +
              'Check ' + ExpandConstant('{app}\uninstall-cleanup.log') + ', close any process it names or make recorded WSL distributions available, then retry uninstall.', mbError, MB_OK);
          Abort;
        end;
        if not RemovePathEntry() then
        begin
          Log('Could not remove Imprint from the user PATH; uninstall cancelled.');
          Abort;
        end;
      end;
    usPostUninstall:
      // Files Inno did not install remain (logs, bytecode caches); a full
      // uninstall leaves nothing behind.
      if not KeepData then
        DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;
