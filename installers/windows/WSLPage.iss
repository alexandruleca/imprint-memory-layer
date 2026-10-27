// Files have been copied before discovery, so the installed CLI can inspect
// WSL. Registration happens only after the user selects profiles and Next.
var
  GWslPage: TInputOptionWizardPage;
  GWslKeys: TStringList;
  GWslDiscovered: Boolean;

procedure CreateWSLPage();
begin
  GWslKeys := TStringList.Create;
  GWslDiscovered := False;
  GWslPage := CreateInputOptionPage(wpInstalling,
    'Connect clients in WSL',
    'Choose Linux client profiles to connect to this Windows Imprint.',
    'Selected clients share this Windows memory store, and their distributions get the imprint command. Existing settings are preserved. WSL distributions may start during discovery. Hook trust remains a client setting.',
    False, False);
end;

procedure DiscoverWSLProfiles();
var
  Code, I, N: Integer;
  ProbeFile, Key, LabelText: string;
  Raw, Saved: AnsiString;
  Rows: TStringList;
begin
  if GWslDiscovered then Exit;
  GWslDiscovered := True;
  ProbeFile := ExpandConstant('{tmp}\imprint-wsl-profiles.txt');
  WizardForm.Refresh;
  if not Exec(ExpandConstant('{app}\bin\imprint.exe'),
    'wsl list --installer-file "' + ProbeFile + '"',
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then Exit;
  if (Code <> 0) or not LoadStringFromFile(ProbeFile, Raw) then Exit;
  Saved := '';
  LoadStringFromFile(ExpandConstant('{app}\.wsl-integrations.txt'), Saved);
  Rows := TStringList.Create;
  try
    Rows.Text := UTF8Decode(Raw);
    for I := 0 to Rows.Count - 1 do
    begin
      Key := Trim(Rows[I]);
      if Key <> '' then
      begin
        GWslKeys.Add(Key);
        LabelText := Key;
        StringChangeEx(LabelText, '|', ' - ', True);
        N := GWslPage.Add(LabelText);
        GWslPage.Values[N] := Pos(#10 + Key + #10, #10 + UTF8Decode(Saved) + #10) > 0;
      end;
    end;
  finally
    Rows.Free;
  end;
  if GWslKeys.Count = 0 then
    GWslPage.SubCaptionLabel.Caption :=
      'No client profiles detected in WSL. You can connect one later with "imprint wsl setup".';
end;

function SaveWSLSelections(): Boolean;
var
  I: Integer;
  Selected: string;
begin
  Selected := '';
  for I := 0 to GWslKeys.Count - 1 do
    if GWslPage.Values[I] then Selected := Selected + GWslKeys[I] + #10;
  Result := SaveStringToFile(ExpandConstant('{app}\.wsl-integrations.txt'),
    UTF8Encode(Selected), False);
  if not Result then MsgBox('Could not save WSL integration choices.', mbError, MB_OK);
end;
