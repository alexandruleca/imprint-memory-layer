package jsonutil

import (
	"os"
	"path/filepath"
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
