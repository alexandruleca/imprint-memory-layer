package jsonutil

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRepairPreservesOptionsAndUserHooks(t *testing.T) {
	path := filepath.Join(t.TempDir(), "settings.json")
	initial := `{"mcpServers":{"imprint":{"command":"old","startup_timeout_sec":75,"env":{"CUSTOM":"keep","PYTHONPATH":"old"}},"other":{"command":"other"}},"hooks":{"Stop":[{"hooks":[{"type":"command","command":"my-user-hook"},{"type":"command","command":"python /old/imprint/hook_entry.py stop /data"}]}]}}`
	if err := os.WriteFile(path, []byte(initial), 0600); err != nil {
		t.Fatal(err)
	}
	spec := map[string]any{"command": "new", "env": map[string]any{"PYTHONPATH": "new"}}
	if _, err := EnsureMCPServer(path, "imprint", spec); err != nil {
		t.Fatal(err)
	}
	if err := SetOwnedHookWithMatcher(path, "Stop", "", "python /new/imprint/hook_entry.py stop /data", 120, true); err != nil {
		t.Fatal(err)
	}
	if err := SetOwnedHookWithMatcher(path, "Stop", "", "python /new/imprint/hook_entry.py stop /data", 120, true); err != nil {
		t.Fatal(err)
	}
	value, err := ReadJSON(path)
	if err != nil {
		t.Fatal(err)
	}
	server := value["mcpServers"].(map[string]any)["imprint"].(map[string]any)
	if server["startup_timeout_sec"] != float64(75) || server["env"].(map[string]any)["CUSTOM"] != "keep" {
		t.Fatal("lost customized server settings")
	}
	group := value["hooks"].(map[string]any)["Stop"].([]any)[0].(map[string]any)
	hooks := group["hooks"].([]any)
	if len(hooks) != 2 || hooks[0].(map[string]any)["command"] != "my-user-hook" {
		t.Fatalf("lost user hook or duplicated owned hook: %v", hooks)
	}
}

// A nil value in the adapter's spec names a key its entry must not have: it
// is removed from an existing entry and never written as null.
func TestNilSpecValueRemovesTheKeyAndKeepsOtherOptions(t *testing.T) {
	path := filepath.Join(t.TempDir(), "settings.json")
	spec := map[string]any{"command": "new", "transport": nil, "env": map[string]any{"STALE": nil, "PYTHONPATH": "new"}}
	if _, err := EnsureMCPServer(path, "imprint", spec); err != nil {
		t.Fatal(err)
	}
	content, _ := os.ReadFile(path)
	if strings.Contains(string(content), "null") || strings.Contains(string(content), "transport") {
		t.Fatalf("created %s", content)
	}
	initial := `{"mcpServers":{"imprint":{"transport":{"type":"stdio","command":"old"},"disabled":true,"env":{"STALE":"x","KEEP":"y"}}}}`
	if err := os.WriteFile(path, []byte(initial), 0600); err != nil {
		t.Fatal(err)
	}
	if changed, err := EnsureMCPServer(path, "imprint", spec); err != nil || !changed {
		t.Fatalf("changed = %v, %v", changed, err)
	}
	if changed, err := EnsureMCPServer(path, "imprint", spec); err != nil || changed {
		t.Fatalf("second run changed = %v, %v", changed, err)
	}
	value, _ := ReadJSON(path)
	server := value["mcpServers"].(map[string]any)["imprint"].(map[string]any)
	env := server["env"].(map[string]any)
	if _, ok := server["transport"]; ok || server["disabled"] != true || server["command"] != "new" || env["KEEP"] != "y" || env["STALE"] != nil {
		t.Fatalf("server = %v", server)
	}
	nested := filepath.Join(t.TempDir(), "nested.json")
	if _, err := EnsureMCPServerNested(nested, []string{"mcp", "servers"}, "imprint", spec); err != nil {
		t.Fatal(err)
	}
	if content, _ := os.ReadFile(nested); strings.Contains(string(content), "null") {
		t.Fatalf("created %s", content)
	}
}

// Imprint's Cursor entries for different matchers of one event coexist, and
// the retired read gate's preToolUse entry goes without touching others.
func TestOwnedCursorHooksKeepOtherMatchersAndRetireTheReadGate(t *testing.T) {
	path := filepath.Join(t.TempDir(), "hooks.json")
	gate := "'/py' '/app/imprint/hook_entry.py' pre-read '/data'"
	user := "/usr/local/bin/audit-reads"
	if err := WriteJSON(path, map[string]any{"version": 1, "hooks": map[string]any{
		"preToolUse": []any{map[string]any{"command": gate, "matcher": "Read|Grep"}, map[string]any{"command": user}},
	}}); err != nil {
		t.Fatal(err)
	}
	search := "'/py' '/app/imprint/hook_entry.py' post-search '/data'"
	reminder := "'/py' '/app/imprint/hook_entry.py' cursor-post-read '/data'"
	for _, step := range [][2]string{{"MCP:search", search}, {"Read|Grep", reminder}, {"MCP:search", search}} {
		if err := SetOwnedCursorHook(path, "postToolUse", step[0], step[1]); err != nil {
			t.Fatal(err)
		}
	}
	if removed, err := RemoveOwnedCursorHooks(path, "preToolUse"); err != nil || removed != 1 {
		t.Fatalf("removed %d, %v", removed, err)
	}
	data, err := ReadJSON(path)
	if err != nil {
		t.Fatal(err)
	}
	hooks := data["hooks"].(map[string]any)
	if post := hooks["postToolUse"].([]any); len(post) != 2 {
		t.Fatalf("postToolUse = %v", post)
	}
	if pre := hooks["preToolUse"].([]any); len(pre) != 1 || pre[0].(map[string]any)["command"] != user {
		t.Fatalf("preToolUse = %v", pre)
	}
}
