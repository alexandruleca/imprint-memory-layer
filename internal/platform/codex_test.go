package platform

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestCodexPathsHonorProfile(t *testing.T) {
	home := filepath.Join(t.TempDir(), "profile with spaces")
	t.Setenv("CODEX_HOME", home)
	for name, got := range map[string]string{
		"config.toml": CodexConfigPath(), "hooks.json": CodexHooksPath(), "AGENTS.md": CodexAgentsPath(),
	} {
		if want := filepath.Join(home, name); got != want {
			t.Errorf("%s = %q, want %q", name, got, want)
		}
	}
}

func TestClaudeConfigDirHonorsProfile(t *testing.T) {
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	if got, want := ClaudeConfigDir(), filepath.Join(HomeDir(), ".claude"); got != want {
		t.Errorf("default ClaudeConfigDir = %q, want %q", got, want)
	}
	custom := filepath.Join(t.TempDir(), "claude profile")
	t.Setenv("CLAUDE_CONFIG_DIR", custom)
	if got := ClaudeConfigDir(); got != custom {
		t.Errorf("ClaudeConfigDir = %q, want %q", got, custom)
	}
}

func TestDataDirHonorsRuntimeOverride(t *testing.T) {
	target := filepath.Join(t.TempDir(), "persistent data")
	t.Setenv("IMPRINT_DATA_DIR", target)
	if got := DataDir("/unrelated/install"); got != target {
		t.Fatalf("DataDir = %q, want %q", got, target)
	}
}

// Electron names its profile after the shipped package.json: productName,
// else name, after electron-builder merges build.extraMetadata into it.
func TestDesktopProfileDirIsElectronsProfile(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "ui-electron", "package.json"))
	if err != nil {
		t.Fatal(err)
	}
	type names struct{ Name, ProductName string }
	var pkg struct {
		names
		Build struct{ ExtraMetadata names }
	}
	if err := json.Unmarshal(raw, &pkg); err != nil {
		t.Fatal(err)
	}
	app := ""
	for _, name := range []string{pkg.Build.ExtraMetadata.ProductName, pkg.ProductName, pkg.Build.ExtraMetadata.Name, pkg.Name} {
		if app == "" {
			app = name
		}
	}
	root, err := os.UserConfigDir()
	if err != nil {
		t.Fatal(err)
	}
	if got, want := DesktopProfileDir(), filepath.Join(root, app); got != want {
		t.Fatalf("DesktopProfileDir = %q, want Electron's profile %q", got, want)
	}
}

// Current Claude Desktop builds read their config from Claude-Data; the
// older Store and standalone layouts follow it, and Claude-Data's README is
// not an install.
func TestClaudeDesktopCandidatesPutClaudeDataFirst(t *testing.T) {
	profile := filepath.Join(t.TempDir(), "alexa")
	install := filepath.Join(profile, "AppData", "Local", "Claude-Data", "claude_pzs8sxrjxfjjc")
	store := filepath.Join(profile, "AppData", "Local", "Packages", "Claude_pzs8sxrjxfjjc", "LocalCache", "Roaming", "Claude")
	for _, dir := range []string{install, store} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(filepath.Dir(install), "README.txt"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	got := claudeDesktopWindowsCandidates(profile)
	want := []string{
		filepath.Join(install, "claude_desktop_config.json"),
		filepath.Join(store, "claude_desktop_config.json"),
		filepath.Join(profile, "AppData", "Roaming", "Claude", "claude_desktop_config.json"),
	}
	if len(got) != len(want) {
		t.Fatalf("candidates = %q, want %q", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("candidates = %q, want %q", got, want)
		}
	}
}
