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
