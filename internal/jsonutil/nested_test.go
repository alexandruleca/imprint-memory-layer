package jsonutil

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func readAsMap(t *testing.T, path string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatalf("unmarshal %s: %v", path, err)
	}
	return m
}

func TestEnsureMCPServerNested_CreatesFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "openclaw.json")
	spec := map[string]any{"command": "py", "args": []any{"-m", "imprint"}}

	added, err := EnsureMCPServerNested(path, []string{"mcp", "servers"}, "imprint", spec)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !added {
		t.Fatalf("expected added=true on fresh create")
	}
	m := readAsMap(t, path)
	mcp, ok := m["mcp"].(map[string]any)
	if !ok {
		t.Fatalf("mcp key missing or wrong type")
	}
	servers, ok := mcp["servers"].(map[string]any)
	if !ok {
		t.Fatalf("mcp.servers key missing or wrong type")
	}
	if _, ok := servers["imprint"].(map[string]any); !ok {
		t.Fatalf("mcp.servers.imprint missing")
	}
}

func TestEnsureMCPServerNested_PreservesSiblings(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "openclaw.json")
	seed := map[string]any{
		"mcp": map[string]any{
			"enabled": true,
			"servers": map[string]any{
				"other": map[string]any{"command": "fake"},
			},
		},
		"unrelated": 42.0,
	}
	if err := WriteJSON(path, seed); err != nil {
		t.Fatalf("write seed: %v", err)
	}

	spec := map[string]any{"command": "py"}
	if _, err := EnsureMCPServerNested(path, []string{"mcp", "servers"}, "imprint", spec); err != nil {
		t.Fatalf("ensure: %v", err)
	}

	m := readAsMap(t, path)
	if m["unrelated"].(float64) != 42 {
		t.Errorf("unrelated clobbered")
	}
	mcp := m["mcp"].(map[string]any)
	if mcp["enabled"] != true {
		t.Errorf("mcp.enabled clobbered")
	}
	servers := mcp["servers"].(map[string]any)
	if _, ok := servers["other"]; !ok {
		t.Errorf("sibling server 'other' clobbered")
	}
	if _, ok := servers["imprint"]; !ok {
		t.Errorf("imprint not added")
	}
}

func TestEnsureMCPServerNested_Idempotent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "openclaw.json")
	spec := map[string]any{"command": "py", "args": []any{"-m", "imprint"}}

	if _, err := EnsureMCPServerNested(path, []string{"mcp", "servers"}, "imprint", spec); err != nil {
		t.Fatalf("first: %v", err)
	}
	added, err := EnsureMCPServerNested(path, []string{"mcp", "servers"}, "imprint", spec)
	if err != nil {
		t.Fatalf("second: %v", err)
	}
	if added {
		t.Errorf("expected added=false on identical rerun")
	}
}

func TestRemoveMCPServerNested_LeavesEmptyParent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "openclaw.json")
	if _, err := EnsureMCPServerNested(path, []string{"mcp", "servers"}, "imprint", map[string]any{"command": "py"}); err != nil {
		t.Fatalf("ensure: %v", err)
	}
	ok, err := RemoveMCPServerNested(path, []string{"mcp", "servers"}, "imprint")
	if err != nil {
		t.Fatalf("remove: %v", err)
	}
	if !ok {
		t.Errorf("expected ok=true on removal")
	}
	m := readAsMap(t, path)
	mcp, ok := m["mcp"].(map[string]any)
	if !ok {
		t.Fatalf("mcp parent removed — should be preserved")
	}
	servers, ok := mcp["servers"].(map[string]any)
	if !ok {
		t.Fatalf("mcp.servers parent removed — should be preserved")
	}
	if len(servers) != 0 {
		t.Errorf("expected empty servers map, got %v", servers)
	}
}

func TestRemoveMCPServerNested_AbsentFileIsNoop(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "does-not-exist.json")
	ok, err := RemoveMCPServerNested(path, []string{"mcp", "servers"}, "imprint")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ok {
		t.Errorf("expected ok=false on missing file")
	}
}

func TestRemoveMCPServerNested_PreservesUnrelatedServers(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "openclaw.json")
	seed := map[string]any{
		"mcp": map[string]any{
			"servers": map[string]any{
				"imprint": map[string]any{"command": "py"},
				"other":   map[string]any{"command": "fake"},
			},
		},
	}
	if err := WriteJSON(path, seed); err != nil {
		t.Fatalf("seed: %v", err)
	}
	if _, err := RemoveMCPServerNested(path, []string{"mcp", "servers"}, "imprint"); err != nil {
		t.Fatalf("remove: %v", err)
	}
	m := readAsMap(t, path)
	servers := m["mcp"].(map[string]any)["servers"].(map[string]any)
	if _, ok := servers["imprint"]; ok {
		t.Errorf("imprint not removed")
	}
	if _, ok := servers["other"]; !ok {
		t.Errorf("sibling 'other' clobbered")
	}
}
