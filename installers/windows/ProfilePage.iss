// Custom wizard page for profile + LLM selection.
// Included from imprint.iss inside the [Code] section, so every line here
// must be valid Inno Setup Pascal Script (// or (* *) comments — NOT the
// `;` style that the top-level .iss sections use).
//
// Exposes to imprint.iss:
//   function SelectedProfile(): 'cpu' | 'gpu'
//   function WithLlmFlag():     '-WithLlm' | ''
// Auto-detects NVIDIA GPU via Win32_VideoController and pre-selects GPU
// when the host looks like it has one.

var
  ProfilePage: TInputOptionWizardPage;
  LlmPage:     TInputOptionWizardPage;

function HasNvidiaGpu(): Boolean;
var
  ResultCode: Integer;
  TempFile:   string;
  Output:     AnsiString;
begin
  Result := False;
  TempFile := ExpandConstant('{tmp}\imprint-gpu-probe.txt');
  if Exec('powershell.exe',
    '-NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name } | Out-File -FilePath ''' + TempFile + ''' -Encoding ascii"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if LoadStringFromFile(TempFile, Output) then
      Result := Pos('NVIDIA', Uppercase(Output)) > 0;
    DeleteFile(TempFile);
  end;
end;

procedure CreateProfilePages();
var
  GpuDefault: Integer;
begin
  ProfilePage := CreateInputOptionPage(
    wpSelectDir,
    'Install profile',
    'Pick the accelerator Imprint should target.',
    'Imprint runs embeddings + optional local LLM inference. Choose GPU if you have a CUDA-capable NVIDIA card; CPU otherwise. You can switch later with "imprint profile set gpu/cpu".',
    True, False);
  ProfilePage.Add('CPU (recommended for laptops without a discrete NVIDIA GPU)');
  ProfilePage.Add('GPU (NVIDIA CUDA — embeddings + LLM run on the GPU)');
  if HasNvidiaGpu() then
    GpuDefault := 1
  else
    GpuDefault := 0;
  ProfilePage.SelectedValueIndex := GpuDefault;

  LlmPage := CreateInputOptionPage(
    ProfilePage.ID,
    'Local LLM tagger',
    'Install the optional local chat + tagger?',
    'Imprint can run Gemma locally via llama-cpp-python for memory tagging and chat. Adds ~200 MB of Python deps (plus GGUF model download on first use). Skip this if you only want embeddings + MCP search.',
    False, False);
  LlmPage.Add('Install llama-cpp-python now (default: off — you can add it later with "imprint profile add-llm")');
end;

function SelectedProfile(Param: string): string;
begin
  if (ProfilePage <> nil) and (ProfilePage.SelectedValueIndex = 1) then
    Result := 'gpu'
  else
    Result := 'cpu';
end;

function WithLlmFlag(Param: string): string;
begin
  if (LlmPage <> nil) and LlmPage.Values[0] then
    Result := '-WithLlm'
  else
    Result := '';
end;
