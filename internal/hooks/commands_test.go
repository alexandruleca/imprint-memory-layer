package hooks

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

// installHookFixture copies Imprint's Python modules into an install whose
// path has spaces and an apostrophe. The hooks run them with a bare
// interpreter: hook_entry and the modules it imports are stdlib-only.
func installHookFixture(t *testing.T) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "Imprint's install with spaces")
	if err := os.MkdirAll(filepath.Join(root, "imprint"), 0755); err != nil {
		t.Fatal(err)
	}
	modules, err := filepath.Glob(filepath.Join("..", "..", "imprint", "*.py"))
	if err != nil || len(modules) == 0 {
		t.Fatalf("no Imprint modules: %v", err)
	}
	for _, module := range modules {
		source, err := os.ReadFile(module)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, "imprint", filepath.Base(module)), source, 0644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestHookCommandsExecuteWithSpacesAndApostrophes(t *testing.T) {
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	interpreter, err := exec.LookPath(python)
	if err != nil {
		t.Skip("Python needed for hook execution fixture")
	}
	root := installHookFixture(t)
	bp := Paths{ProjectDir: root, VenvPython: interpreter, DataDir: filepath.Join(root, "user's data")}
	run := func(command string) error {
		child := hookShellCommand(command)
		child.Stdin = bytes.NewBufferString(`{"session_id":"fixture"}`)
		output, err := child.CombinedOutput()
		if err != nil {
			t.Logf("%s: %s", command, output)
		}
		return err
	}
	if err := run(PreReadGateCommand(bp)); err == nil {
		t.Fatal("unsearched session should be gated")
	}
	if err := run(PostSearchSentinelCommand(bp)); err != nil {
		t.Fatal(err)
	}
	if err := run(PreReadGateCommand(bp)); err != nil {
		t.Fatal(err)
	}
}

// Copilot runs a command hook in PowerShell on Windows and bash elsewhere,
// and reads a SessionStart hook's context from top-level additionalContext.
func TestCopilotHookCommandsExecuteInCopilotsShell(t *testing.T) {
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	interpreter, err := exec.LookPath(python)
	if err != nil {
		t.Skip("Python needed for hook execution fixture")
	}
	root := installHookFixture(t)
	bp := Paths{ProjectDir: root, VenvPython: interpreter, DataDir: filepath.Join(root, "user's data")}
	run := func(command, payload string) []byte {
		t.Helper()
		child := copilotShellCommand(command)
		child.Stdin = bytes.NewBufferString(payload)
		output, err := child.Output()
		if err != nil {
			t.Fatalf("%s: %v %s", command, err, output)
		}
		return output
	}
	var start map[string]any
	if err := json.Unmarshal(run(CopilotSessionStartCommand(bp), `{"hook_event_name":"SessionStart","session_id":"s","cwd":"/w","source":"startup"}`), &start); err != nil {
		t.Fatal(err)
	}
	if context, _ := start["additionalContext"].(string); context == "" || start["hookSpecificOutput"] != nil {
		t.Fatalf("SessionStart output %v", start)
	}
	var stop map[string]any
	if err := json.Unmarshal(run(CopilotStopCommand(bp), `{"hook_event_name":"Stop","session_id":"s","stop_reason":"end_turn","stop_hook_active":false}`), &stop); err != nil || len(stop) != 0 {
		t.Fatalf("Stop output %v, %v", stop, err)
	}
}
