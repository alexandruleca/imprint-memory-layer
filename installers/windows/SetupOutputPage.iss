// Wizard page that streams imprint bootstrap output inline.
// Requires Inno Setup 6.1+ (CreateCallback).
// Included from imprint.iss [Code] section.
//
// Exposes:
//   procedure CreateSetupOutputPage()
//   procedure StartBootstrapOnPage()

var
  GOutputPage:  TWizardPage;
  GOutputMemo:  TNewMemo;
  GOutputLabel: TNewStaticText;
  GTimerID:     UINT_PTR;
  GBootDone:    Boolean;
  GBootOk:      Boolean;

function SetTimer(hWnd: HWND; nIDEvent: UINT_PTR; uElapse: UINT;
    lpTimerFunc: LongWord): UINT_PTR;
  external 'SetTimer@user32.dll stdcall';
function KillTimer(hWnd: HWND; uIDEvent: UINT_PTR): BOOL;
  external 'KillTimer@user32.dll stdcall';
function SendMessageScroll(hWnd: HWND; Msg: UINT; wParam: UINT_PTR; lParam: INT_PTR): INT_PTR;
  external 'SendMessageW@user32.dll stdcall';

const
  WM_VSCROLL = $0115;
  SB_BOTTOM  = 7;

// Called by Win32 timer every 300 ms. Reads the log file and updates the memo.
procedure BootstrapTimerProc(hWnd: HWND; uMsg: UINT; idEvent: UINT_PTR; dwTime: DWORD);
var
  LogContent: AnsiString;
  LogPath, Sentinel: string;
begin
  if GBootDone then Exit;

  LogPath  := ExpandConstant('{app}\first-run.log');
  Sentinel := ExpandConstant('{app}\.first-run.done');

  if LoadStringFromFile(LogPath, LogContent) then
  begin
    GOutputMemo.Lines.Text := String(LogContent);
    SendMessageScroll(GOutputMemo.Handle, WM_VSCROLL, SB_BOTTOM, 0);
  end;

  // Success: PS1 created sentinel after writing "Setup complete." to log.
  if FileExists(Sentinel) then
  begin
    KillTimer(0, GTimerID);
    GTimerID  := 0;
    GBootDone := True;
    GBootOk   := True;
    GOutputLabel.Caption := 'Setup complete — click Next to finish installation.';
    WizardForm.NextButton.Enabled := True;
    Exit;
  end;

  // Failure: PS1 wrote "FAILED:" to log and exited non-zero.
  if (Length(LogContent) > 0) and (Pos('FAILED:', String(LogContent)) > 0) then
  begin
    KillTimer(0, GTimerID);
    GTimerID  := 0;
    GBootDone := True;
    GBootOk   := False;
    GOutputLabel.Caption :=
      'Setup failed — see output above. Use "Repair Imprint" from the Start menu to retry.';
    WizardForm.NextButton.Enabled := True;
  end;
end;

procedure CreateSetupOutputPage();
begin
  GOutputPage := CreateCustomPage(
    wpInstalling,
    'Setting up Imprint',
    'Downloading Python and packages — first install only, may take a few minutes.'
  );

  GOutputLabel := TNewStaticText.Create(GOutputPage);
  GOutputLabel.Parent   := GOutputPage.Surface;
  GOutputLabel.Top      := 0;
  GOutputLabel.Width    := GOutputPage.SurfaceWidth;
  GOutputLabel.Caption  := 'Running first-run setup...';
  GOutputLabel.AutoSize := True;

  GOutputMemo := TNewMemo.Create(GOutputPage);
  GOutputMemo.Parent     := GOutputPage.Surface;
  GOutputMemo.Top        := 24;
  GOutputMemo.Left       := 0;
  GOutputMemo.Width      := GOutputPage.SurfaceWidth;
  GOutputMemo.Height     := GOutputPage.SurfaceHeight - GOutputMemo.Top;
  GOutputMemo.ScrollBars := ssVertical;
  GOutputMemo.ReadOnly   := True;
  GOutputMemo.Font.Name  := 'Consolas';
  GOutputMemo.Font.Size  := 8;
end;

procedure StartBootstrapOnPage();
var
  AppDir, PsFile, PsArgs: string;
  ResultCode: Integer;
begin
  GBootDone := False;
  GBootOk   := False;
  GTimerID  := 0;

  WizardForm.NextButton.Enabled := False;
  WizardForm.BackButton.Enabled := False;

  AppDir := ExpandConstant('{app}');
  PsFile := AppDir + '\imprint-setup.ps1';

  // Reset state from any prior run so the timer doesn't see a stale sentinel.
  DeleteFile(AppDir + '\.first-run.done');
  SaveStringToFile(AppDir + '\first-run.log', '', False);
  GOutputMemo.Lines.Text := '';

  PsArgs :=
    '-NoProfile -ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden ' +
    '-File "' + PsFile + '" ' +
    '-InstallDir "' + AppDir + '" ' +
    '-Profile '    + SelectedProfile('') + ' ' +
    WithLlmFlag('');

  if not Exec('powershell.exe', PsArgs, AppDir, SW_HIDE, ewNoWait, ResultCode) then
  begin
    GOutputLabel.Caption := 'Could not start setup script — reinstall Imprint.';
    WizardForm.NextButton.Enabled := True;
    Exit;
  end;

  // Poll the log file every 300 ms; BootstrapTimerProc enables Next when done.
  GTimerID := SetTimer(0, 0, 300, CreateCallback(@BootstrapTimerProc));
end;
