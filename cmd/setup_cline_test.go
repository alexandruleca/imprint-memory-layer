package cmd

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/platform"
)

// Cline 4 reads one MCP settings file and its global rules directory on every
// surface: setup writes those, honors Cline's environment overrides, and no
// longer writes the VS Code extension storage (ADR 0056).
func TestSetupClineWritesItsSettingsAndRuleWhereClineReadsThem(t *testing.T) {
	home := clineHome(t)
	project := filepath.Join(home, "runtime")
	runtime := backendPaths{ProjectDir: project, VenvPython: filepath.Join(project, "python"), DataDir: filepath.Join(project, "data")}
	backend := func() backendPaths { return runtime }

	// Not installed: nothing is written.
	SetupCline(backend)
	if entries, _ := os.ReadDir(home); len(entries) != 0 {
		t.Fatalf("setup without Cline wrote %v", entries)
	}

	cline := filepath.Join(home, "cline home")
	t.Setenv("CLINE_DIR", cline)
	t.Setenv("CLINE_DATA_DIR", filepath.Join(home, "cline data"))
	if err := os.MkdirAll(cline, 0700); err != nil {
		t.Fatal(err)
	}
	settings := filepath.Join(home, "cline data", "settings", "cline_mcp_settings.json")
	rule := filepath.Join(cline, "rules", "imprint.md")
	if platform.ClineMCPSettingsPath() != settings || platform.ClineRulesPath() != rule ||
		platform.ClineSessionsDir() != filepath.Join(home, "cline data", "sessions") {
		t.Fatalf("paths: %s, %s, %s", platform.ClineMCPSettingsPath(), platform.ClineRulesPath(), platform.ClineSessionsDir())
	}
	// `cline mcp add` writes a nested transport, which Cline reads first.
	writeCleanupFixture(t, settings, `{"mcpServers":{"other":{"command":"keep-other"},`+
		`"imprint":{"transport":{"type":"stdio","command":"/stale/imprint"},"disabled":true}}}`)
	storage := filepath.Join(platform.VSCodeUserDirs()[0], "globalStorage", "saoudrizwan.claude-dev")
	if err := os.MkdirAll(storage, 0700); err != nil {
		t.Fatal(err)
	}

	SetupCline(backend)
	first, _ := os.ReadFile(settings)
	SetupCline(backend)
	second, _ := os.ReadFile(settings)
	if string(first) != string(second) {
		t.Fatal("setup is not idempotent")
	}
	data, err := jsonutil.ReadJSON(settings)
	if err != nil {
		t.Fatal(err)
	}
	servers := data["mcpServers"].(map[string]any)
	entry := servers["imprint"].(map[string]any)
	if entry["command"] != runtime.VenvPython || entry["transport"] != nil || entry["disabled"] != true || servers["other"] == nil {
		t.Fatalf("settings = %v", data)
	}
	if content, _ := os.ReadFile(rule); string(content) != instructions.ClineRule {
		t.Fatalf("rule = %q", content)
	}
	for _, stale := range []string{filepath.Join(home, ".clinerules"), filepath.Join(storage, "settings")} {
		if _, err := os.Stat(stale); !os.IsNotExist(err) {
			t.Fatalf("setup wrote %s", stale)
		}
	}
	selected, _ := selectClients("cline")
	if ok, err := isConnected(selected[0], project); err != nil || !ok {
		t.Fatalf("connected = %v, %v", ok, err)
	}

	// CLINE_MCP_SETTINGS_PATH names the settings file itself.
	custom := filepath.Join(home, "elsewhere.json")
	t.Setenv("CLINE_MCP_SETTINGS_PATH", custom)
	SetupCline(backend)
	if data, err := jsonutil.ReadJSON(custom); err != nil || data["mcpServers"].(map[string]any)["imprint"] == nil {
		t.Fatalf("custom settings = %v, %v", data, err)
	}

	t.Setenv("CLINE_MCP_SETTINGS_PATH", "")
	if err := disconnectClients(selected, project); err != nil {
		t.Fatal(err)
	}
	if ok, _ := isConnected(selected[0], project); ok {
		t.Fatal("disconnect left Cline connected")
	}
	if _, err := os.Stat(rule); !os.IsNotExist(err) {
		t.Fatal("disconnect left the rule")
	}
}
