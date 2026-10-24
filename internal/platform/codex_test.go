package platform

import (
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
