package cmd

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/platform"
)

func openCodeHome(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("XDG_CONFIG_HOME", "")
	t.Setenv("XDG_DATA_HOME", "")
	t.Setenv("OPENCODE_CONFIG_DIR", "")
	t.Setenv("PATH", "")
	return home
}

// OpenCode gets a local MCP server and a listed instructions file in its
// global opencode.json, and a plugin that learns idle sessions; AGENTS.md is
// left alone, and disconnect removes exactly what setup added (ADR 0124).
func TestSetupOpenCodeRegistersServerInstructionsAndPlugin(t *testing.T) {
	home := openCodeHome(t)
	project := filepath.Join(home, "runtime")
	runtime := backendPaths{ProjectDir: project, VenvPython: filepath.Join(project, "python"), DataDir: filepath.Join(project, "data")}
	backend := func() backendPaths { return runtime }

	// Not installed: nothing is written.
	SetupOpenCode(backend)
	if entries, _ := os.ReadDir(home); len(entries) != 0 {
		t.Fatalf("setup without OpenCode wrote %v", entries)
	}

	configDir := filepath.Join(home, ".config", "opencode")
	config := filepath.Join(configDir, "opencode.json")
	if platform.OpenCodeConfigPath() != config || platform.OpenCodeDataDir() != filepath.Join(home, ".local", "share", "opencode") {
		t.Fatalf("paths: %s, %s", platform.OpenCodeConfigPath(), platform.OpenCodeDataDir())
	}
	if err := os.MkdirAll(configDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(config, []byte(`{"$schema":"https://opencode.ai/config.json","instructions":["mine.md"],`+
		`"mcp":{"other":{"type":"remote","url":"https://example.com"}}}`), 0o600); err != nil {
		t.Fatal(err)
	}

	SetupOpenCode(backend)
	first, _ := os.ReadFile(config)
	SetupOpenCode(backend)
	second, _ := os.ReadFile(config)
	if string(first) != string(second) {
		t.Fatal("setup is not idempotent")
	}
	data, err := jsonutil.ReadJSON(config)
	if err != nil {
		t.Fatal(err)
	}
	servers := data["mcp"].(map[string]any)
	entry := servers["imprint"].(map[string]any)
	command, _ := entry["command"].([]any)
	if entry["type"] != "local" || len(command) != 3 || command[0] != runtime.VenvPython || servers["other"] == nil {
		t.Fatalf("config = %v", data)
	}
	rule := platform.OpenCodeRulePath()
	if list := data["instructions"].([]any); !slices.Equal(list, []any{"mine.md", rule}) {
		t.Fatalf("instructions = %v", list)
	}
	if content, _ := os.ReadFile(rule); string(content) != instructions.OpenCodeRule || strings.Contains(string(content), "mcp__imprint__") {
		t.Fatalf("rule = %q", content)
	}
	plugin, _ := os.ReadFile(platform.OpenCodePluginPath())
	if !strings.Contains(string(plugin), "opencode-idle") || !strings.Contains(string(plugin), "hook_entry.py") {
		t.Fatalf("plugin = %s", plugin)
	}
	if _, err := os.Stat(filepath.Join(configDir, "AGENTS.md")); !os.IsNotExist(err) {
		t.Fatal("setup wrote AGENTS.md, which would hide ~/.claude/CLAUDE.md from OpenCode")
	}
	selected, _ := selectClients("opencode")
	if ok, err := isConnected(selected[0], project); err != nil || !ok {
		t.Fatalf("connected = %v, %v", ok, err)
	}

	if err := disconnectClients(selected, project); err != nil {
		t.Fatal(err)
	}
	if ok, _ := isConnected(selected[0], project); ok {
		t.Fatal("disconnect left OpenCode connected")
	}
	data, _ = jsonutil.ReadJSON(config)
	if list := data["instructions"].([]any); !slices.Equal(list, []any{"mine.md"}) || data["mcp"].(map[string]any)["other"] == nil {
		t.Fatalf("disconnect changed the user's config: %v", data)
	}
	for _, path := range []string{rule, platform.OpenCodePluginPath()} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("disconnect left %s", path)
		}
	}
}
