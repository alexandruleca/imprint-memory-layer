// Custom wizard page for the install profile.
// Included from imprint.iss inside the [Code] section, so every line here
// must be valid Inno Setup Pascal Script (// or (* *) comments - NOT the
// `;` style that the top-level .iss sections use).
//
// Exposes to imprint.iss:
//   function SelectedProfile(): 'cpu' | 'gpu'
// GPU is pre-selected: setup keeps llama.cpp's GPU build only when it finds
// a GPU, and installs the CPU build otherwise (ADR 0049, 0086).

var
  ProfilePage: TInputOptionWizardPage;

procedure CreateProfilePages();
begin
  ProfilePage := CreateInputOptionPage(
    wpSelectDir,
    'Install profile',
    'Pick where Imprint''s local models run.',
    'GPU runs embeddings and the local LLM (tagging, chat) on an NVIDIA, AMD or Intel GPU through Vulkan when one is found, and on the CPU otherwise. No CUDA or other toolkit is needed. You can switch with "imprint profile set gpu/cpu".',
    True, False);
  ProfilePage.Add('CPU (local models on the processor)');
  ProfilePage.Add('GPU (local models on the GPU when one is found)');
  ProfilePage.SelectedValueIndex := 1;
end;

function SelectedProfile(Param: string): string;
begin
  if (ProfilePage <> nil) and (ProfilePage.SelectedValueIndex = 1) then
    Result := 'gpu'
  else
    Result := 'cpu';
end;
