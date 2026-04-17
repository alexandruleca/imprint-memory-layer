package tomlutil

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func spec(cmd string) map[string]any {
	return map[string]any{
		"command": cmd,
		"args":    []any{"-m", "imprint"},
		"env": map[string]any{
			"PYTHONPATH":       "/proj",
			"IMPRINT_DATA_DIR": "/proj/data",
		},
	}
}

func TestEnsureMCPServer_CreatesFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "nested", "config.toml")

	added, err := EnsureMCPServer(path, "imprint", spec("/venv/bin/python"))
	if err != nil {
		t.Fatalf("EnsureMCPServer: %v", err)
	}
	if !added {
		t.Fatalf("expected added=true on create")
	}

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	content := string(data)

	for _, want := range []string{
		"[mcp_servers.imprint]",
		`command = "/venv/bin/python"`,
		`args = ["-m", "imprint"]`,
		`env = {IMPRINT_DATA_DIR = "/proj/data", PYTHONPATH = "/proj"}`,
	} {
		if !strings.Contains(content, want) {
			t.Errorf("missing %q in output:\n%s", want, content)
		}
	}
}

func TestEnsureMCPServer_Idempotent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.toml")

	s := spec("/venv/bin/python")
	if _, err := EnsureMCPServer(path, "imprint", s); err != nil {
		t.Fatal(err)
	}
	added, err := EnsureMCPServer(path, "imprint", s)
	if err != nil {
		t.Fatal(err)
	}
	if added {
		t.Fatalf("second call should report added=false")
	}
}

func TestEnsureMCPServer_ReplacesExistingSection(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.toml")

	if _, err := EnsureMCPServer(path, "imprint", spec("/old/python")); err != nil {
		t.Fatal(err)
	}
	added, err := EnsureMCPServer(path, "imprint", spec("/new/python"))
	if err != nil {
		t.Fatal(err)
	}
	if !added {
		t.Fatalf("changed command should report added=true")
	}
	data, _ := os.ReadFile(path)
	content := string(data)
	if strings.Contains(content, "/old/python") {
		t.Errorf("old command still present:\n%s", content)
	}
	if !strings.Contains(content, "/new/python") {
		t.Errorf("new command missing:\n%s", content)
	}
	// Only one header should remain.
	if n := strings.Count(content, "[mcp_servers.imprint]"); n != 1 {
		t.Errorf("expected exactly one section header, got %d", n)
	}
}

func TestEnsureMCPServer_PreservesOtherSections(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.toml")
	preamble := "# top comment\n" +
		"[other]\nfoo = \"bar\"\n\n" +
		"[mcp_servers.someoneelse]\ncommand = \"/bin/other\"\n\n" +
		"[trailing]\nbaz = 1\n"
	if err := os.WriteFile(path, []byte(preamble), 0644); err != nil {
		t.Fatal(err)
	}

	if _, err := EnsureMCPServer(path, "imprint", spec("/venv/bin/python")); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(path)
	content := string(data)
	for _, want := range []string{"[other]", `foo = "bar"`, "[mcp_servers.someoneelse]", "[trailing]", "baz = 1", "[mcp_servers.imprint]"} {
		if !strings.Contains(content, want) {
			t.Errorf("lost %q:\n%s", want, content)
		}
	}
}

func TestRemoveMCPServer(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.toml")
	if _, err := EnsureMCPServer(path, "imprint", spec("/venv/bin/python")); err != nil {
		t.Fatal(err)
	}
	// Add a neighbor section to make sure we don't nuke it.
	data, _ := os.ReadFile(path)
	updated := string(data) + "\n[keepme]\nx = 1\n"
	if err := os.WriteFile(path, []byte(updated), 0644); err != nil {
		t.Fatal(err)
	}

	removed, err := RemoveMCPServer(path, "imprint")
	if err != nil {
		t.Fatal(err)
	}
	if !removed {
		t.Fatalf("expected removed=true")
	}

	final, _ := os.ReadFile(path)
	content := string(final)
	if strings.Contains(content, "[mcp_servers.imprint]") {
		t.Errorf("section still present:\n%s", content)
	}
	if !strings.Contains(content, "[keepme]") {
		t.Errorf("removed unrelated section:\n%s", content)
	}

	// Second removal reports false.
	removed, err = RemoveMCPServer(path, "imprint")
	if err != nil {
		t.Fatal(err)
	}
	if removed {
		t.Fatalf("second remove should report false")
	}
}

func TestRemoveMCPServer_FileMissing(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "nope.toml")
	removed, err := RemoveMCPServer(path, "imprint")
	if err != nil {
		t.Fatal(err)
	}
	if removed {
		t.Fatalf("expected removed=false for missing file")
	}
}
