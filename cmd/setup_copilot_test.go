package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/hunter/imprint/internal/hooks"
	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/platform"
)

// copilotProfile points Copilot's environment at a disposable home, with no
// copilot command on PATH.
func copilotProfile(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("APPDATA", filepath.Join(home, "AppData", "Roaming"))
	t.Setenv("COPILOT_HOME", "")
	t.Setenv("PATH", t.TempDir())
	return home
}

// Copilot's surfaces share the Copilot home: setup writes its MCP servers,
// instructions and Imprint's hooks file there, keeps unrelated settings, and
// no longer writes VS Code's user mcp.json (ADR 0057).
func TestSetupCopilotWritesTheCopilotHome(t *testing.T) {
	home := copilotProfile(t)
	project := filepath.Join(home, "runtime")
	runtimePaths := backendPaths{ProjectDir: project, VenvPython: filepath.Join(project, "python"), DataDir: filepath.Join(project, "data")}
	backend := func() backendPaths { return runtimePaths }

	SetupCopilot(backend)
	if entries, _ := os.ReadDir(home); len(entries) != 0 {
		t.Fatalf("setup without Copilot wrote %v", entries)
	}

	copilot := filepath.Join(home, "copilot home")
	t.Setenv("COPILOT_HOME", copilot)
	writeCleanupFixture(t, filepath.Join(copilot, "mcp-config.json"), `{"mcpServers":{"other":{"type":"stdio","command":"keep-other","tools":["*"]}},"keep":true}`)
	writeCleanupFixture(t, filepath.Join(copilot, "copilot-instructions.md"), "Answer in British English.\n")
	writeCleanupFixture(t, filepath.Join(copilot, "hooks", "mine.json"), `{"version":1,"hooks":{"Stop":[{"command":"my-hook"}]}}`)
	vscode := platform.VSCodeUserDirs()[0]
	if err := os.MkdirAll(vscode, 0700); err != nil {
		t.Fatal(err)
	}

	SetupCopilot(backend)
	files := map[string][]byte{}
	for _, name := range []string{"mcp-config.json", "copilot-instructions.md", filepath.Join("hooks", "imprint.json")} {
		files[name], _ = os.ReadFile(filepath.Join(copilot, name))
	}
	SetupCopilot(backend)
	for name, first := range files {
		if second, _ := os.ReadFile(filepath.Join(copilot, name)); string(second) != string(first) {
			t.Fatalf("%s changed on a second run", name)
		}
	}
	if backups, _ := filepath.Glob(filepath.Join(copilot, "*.imprint-*.bak")); len(backups) != 2 {
		t.Fatalf("backups %v: each preexisting file is backed up once", backups)
	}

	data, err := jsonutil.ReadJSON(filepath.Join(copilot, "mcp-config.json"))
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(data["mcpServers"].(map[string]any)["imprint"])
	want, _ := json.Marshal(map[string]any{"type": "stdio", "command": runtimePaths.VenvPython, "args": []any{"-m", "imprint"},
		"env": map[string]any{"PYTHONPATH": project, "IMPRINT_DATA_DIR": runtimePaths.DataDir}, "tools": []any{"*"}})
	if string(encoded) != string(want) || data["keep"] != true || data["mcpServers"].(map[string]any)["other"] == nil {
		t.Fatalf("mcp-config.json = %v", data)
	}
	if rules := string(files["copilot-instructions.md"]); !strings.HasPrefix(rules, "Answer in British English.\n") ||
		!strings.Contains(rules, instructions.MarkerStart+"\n"+instructions.CopilotRule+instructions.MarkerEnd) {
		t.Fatalf("instructions = %q", rules)
	}
	hp := hooks.Paths{ProjectDir: project, VenvPython: runtimePaths.VenvPython, DataDir: runtimePaths.DataDir}
	if got := string(files[filepath.Join("hooks", "imprint.json")]); got != string(copilotHooks(hooks.CopilotSessionStartCommand(hp), hooks.CopilotStopCommand(hp))) {
		t.Fatalf("hooks = %s", got)
	}
	var hookDoc struct {
		Version int                         `json:"version"`
		Hooks   map[string][]map[string]any `json:"hooks"`
	}
	if err := json.Unmarshal(files[filepath.Join("hooks", "imprint.json")], &hookDoc); err != nil || hookDoc.Version != 1 ||
		hookDoc.Hooks["SessionStart"][0]["timeoutSec"] != float64(10) || hookDoc.Hooks["Stop"][0]["timeoutSec"] != float64(120) ||
		!strings.Contains(hookDoc.Hooks["SessionStart"][0]["command"].(string), " copilot-session-start ") ||
		!strings.Contains(hookDoc.Hooks["Stop"][0]["command"].(string), " stop ") {
		t.Fatalf("hook document = %+v, %v", hookDoc, err)
	}
	if _, err := os.Stat(filepath.Join(vscode, "mcp.json")); !os.IsNotExist(err) {
		t.Fatal("setup wrote VS Code's user mcp.json")
	}
	selected, _ := selectClients("copilot")
	if ok, err := isConnected(selected[0], project); err != nil || !ok {
		t.Fatalf("connected = %v, %v", ok, err)
	}

	// What setup wrote before ADR 0057, beside the current registration.
	legacyEntry := `{"servers":{"imprint":{"type":"stdio","command":"` + filepath.ToSlash(runtimePaths.VenvPython) + `"},"other":{"command":"keep-other"}}}`
	writeCleanupFixture(t, filepath.Join(vscode, "mcp.json"), legacyEntry)
	prompts := filepath.Join(vscode, "prompts", "imprint.instructions.md")
	writeCleanupFixture(t, prompts, instructions.MergeManaged("", instructions.MarkerStart+"\n"+instructions.CopilotRule+instructions.MarkerEnd+"\n"))

	if err := disconnectClients(selected, project); err != nil {
		t.Fatal(err)
	}
	data, _ = jsonutil.ReadJSON(filepath.Join(copilot, "mcp-config.json"))
	if servers := data["mcpServers"].(map[string]any); servers["imprint"] != nil || servers["other"] == nil {
		t.Fatalf("mcp-config.json after disconnect = %v", data)
	}
	if rules, _ := os.ReadFile(filepath.Join(copilot, "copilot-instructions.md")); !strings.HasPrefix(string(rules), "Answer in British English.\n") ||
		strings.Contains(string(rules), instructions.MarkerStart) {
		t.Fatalf("instructions after disconnect = %q", rules)
	}
	for _, gone := range []string{filepath.Join(copilot, "hooks", "imprint.json"), prompts} {
		if _, err := os.Stat(gone); !os.IsNotExist(err) {
			t.Fatalf("%s remains", gone)
		}
	}
	if content, _ := os.ReadFile(filepath.Join(copilot, "hooks", "mine.json")); !strings.Contains(string(content), "my-hook") {
		t.Fatal("disconnect touched the user's hooks")
	}
	if content, _ := os.ReadFile(filepath.Join(vscode, "mcp.json")); strings.Contains(string(content), `"imprint"`) || !strings.Contains(string(content), "keep-other") {
		t.Fatalf("VS Code mcp.json after disconnect = %s", content)
	}
}

// Another runtime's registration protects its instructions and hooks file,
// and an edited prompts file from before ADR 0057 is the user's.
func TestDisconnectCopilotPreservesWhatIsNotThisRuntimes(t *testing.T) {
	home := copilotProfile(t)
	copilot := filepath.Join(home, ".copilot")
	writeCleanupFixture(t, filepath.Join(copilot, "mcp-config.json"), `{"mcpServers":{"imprint":{"type":"stdio","command":"/other/runtime/python"}}}`)
	other := `{"version":1,"hooks":{"Stop":[{"command":"'/other/runtime/python' '/other/runtime/imprint/hook_entry.py' stop '/other/data'"}]}}`
	writeCleanupFixture(t, filepath.Join(copilot, "hooks", "imprint.json"), other)
	prompts := filepath.Join(platform.VSCodeUserDirs()[0], "prompts", "imprint.instructions.md")
	edited := "My own notes.\n\n" + instructions.MarkerStart + "\n" + instructions.CopilotRule + instructions.MarkerEnd + "\n"
	writeCleanupFixture(t, prompts, edited)
	selected, _ := selectClients("copilot")
	if err := disconnectClients(selected, filepath.Join(home, "runtime")); err != nil {
		t.Fatal(err)
	}
	if content, _ := os.ReadFile(filepath.Join(copilot, "hooks", "imprint.json")); string(content) != other {
		t.Fatal("another runtime's hooks file was changed")
	}
	if content, _ := os.ReadFile(prompts); string(content) != edited {
		t.Fatal("an edited prompts file was changed")
	}
}

// Copilot runs a command hook in PowerShell on Windows and bash elsewhere.
func TestCopilotHookCommandsSuitTheirShell(t *testing.T) {
	hp := hooks.Paths{ProjectDir: `/opt/Imprint's runtime`, VenvPython: "/opt/py", DataDir: "/opt/data"}
	command := hooks.CopilotStopCommand(hp)
	if runtime.GOOS == "windows" {
		if !strings.HasPrefix(command, "& '") || !strings.Contains(command, "Imprint''s runtime") {
			t.Fatalf("PowerShell command %s", command)
		}
	} else if command != hooks.StopCommand(hp) {
		t.Fatalf("bash command %s", command)
	}
}
