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
	// A read before searching is reminded, never refused (ADR 0142).
	for _, command := range []string{PreReadGateCommand(bp), PostSearchSentinelCommand(bp), PreReadGateCommand(bp),
		CursorPostReadCommand(bp)} {
		if err := run(command); err != nil {
			t.Fatal(err)
		}
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

// Claude Code's prompt hook is a shell script, not Python (ADR 0212): it
// prints what the conversation's Imprint server prepared, once, in an
// install whose path has spaces and an apostrophe.
func TestPromptNoticesCommandPrintsOnce(t *testing.T) {
	shell := exec.Command("sh", "-c")
	if runtime.GOOS == "windows" {
		// Claude Code runs hooks in Git Bash on Windows, not cmd.exe.
		bash := filepath.Join(os.Getenv("ProgramFiles"), "Git", "bin", "bash.exe")
		if _, err := os.Stat(bash); err != nil {
			t.Skip("Git Bash needed")
		}
		shell = exec.Command(bash, "-c")
	} else if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh needed")
	}
	root := filepath.Join(t.TempDir(), "Imprint's install with spaces")
	if err := os.MkdirAll(filepath.Join(root, "imprint"), 0755); err != nil {
		t.Fatal(err)
	}
	script, err := os.ReadFile(filepath.Join("..", "..", "imprint", "prompt_notices.sh"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "imprint", "prompt_notices.sh"), script, 0644); err != nil {
		t.Fatal(err)
	}
	bp := Paths{ProjectDir: root, VenvPython: "unused", DataDir: filepath.Join(root, "user's data")}
	notices := filepath.Join(bp.DataDir, "prompt-notices")
	if err := os.MkdirAll(notices, 0755); err != nil {
		t.Fatal(err)
	}
	prepared := `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"New comment"}}`
	if err := os.WriteFile(filepath.Join(notices, "conversation-1.json"), []byte(prepared), 0644); err != nil {
		t.Fatal(err)
	}
	run := func() string {
		child := exec.Command(shell.Path, append(shell.Args[1:], PromptNoticesCommand(bp))...)
		child.Stdin = bytes.NewBufferString(`{"session_id":"conversation-1","hook_event_name":"UserPromptSubmit","prompt":"hi"}`)
		output, err := child.CombinedOutput()
		if err != nil {
			t.Fatalf("%s: %v", output, err)
		}
		return string(output)
	}
	if got := run(); got != prepared {
		t.Fatalf("first prompt: %q", got)
	}
	if got := run(); got != "" {
		t.Fatalf("second prompt: %q", got)
	}
	var decoded map[string]any
	if json.Unmarshal([]byte(prepared), &decoded) != nil {
		t.Fatal("fixture")
	}
}
