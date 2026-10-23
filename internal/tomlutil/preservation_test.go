package tomlutil

import (
	"github.com/pelletier/go-toml/v2"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSetupPreservesCustomOptionsAndNestedEnvironment(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.toml")
	original := `# unrelated preference
model = "example"
[mcp_servers.other]
command = "other"
[mcp_servers.imprint] # owned server
command = "old"
args = [
 "-m", "imprint",
]
startup_timeout_sec = 75
enabled = false
[mcp_servers.imprint.env]
CUSTOM_TOKEN = "keep"
PYTHONPATH = "old"
[mcp_servers.imprint.tools.search]
approval_mode = "prompt"
[features]
custom = true
`
	if err := os.WriteFile(path, []byte(original), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := EnsureMCPServer(path, "imprint", spec("/new/python")); err != nil {
		t.Fatal(err)
	}
	content, _ := os.ReadFile(path)
	var parsed map[string]any
	if err := toml.Unmarshal(content, &parsed); err != nil {
		t.Fatal(err)
	}
	server := parsed["mcp_servers"].(map[string]any)["imprint"].(map[string]any)
	if server["startup_timeout_sec"] != int64(75) || server["enabled"] != false {
		t.Fatalf("lost custom server options: %v", server)
	}
	if server["env"].(map[string]any)["CUSTOM_TOKEN"] != "keep" {
		t.Fatal("lost custom environment")
	}
	if !strings.Contains(string(content), "# unrelated preference") || !strings.Contains(string(content), "[mcp_servers.other]") {
		t.Fatal("changed unrelated configuration")
	}
	changed, err := EnsureMCPServer(path, "imprint", spec("/new/python"))
	if err != nil || changed {
		t.Fatalf("not idempotent: %v %v", changed, err)
	}
	backups, _ := filepath.Glob(path + ".imprint-*.bak")
	if len(backups) != 1 {
		t.Fatalf("expected one recoverable original, got %d", len(backups))
	}
	if _, err := RemoveMCPServer(path, "imprint"); err != nil {
		t.Fatal(err)
	}
	removed, _ := os.ReadFile(path)
	if strings.Contains(string(removed), "mcp_servers.imprint") {
		t.Fatal("orphaned owned subtable")
	}
}

func TestMalformedConfigIsNotOverwritten(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.toml")
	original := []byte("[broken\nsecret = 'keep'\n")
	if err := os.WriteFile(path, original, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := EnsureMCPServer(path, "imprint", spec("/python")); err == nil {
		t.Fatal("accepted invalid TOML")
	}
	actual, _ := os.ReadFile(path)
	if string(actual) != string(original) {
		t.Fatal("overwrote malformed config")
	}
}
