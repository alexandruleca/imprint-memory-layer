package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hunter/imprint/internal/hooks"
)

// Claude Code gets the prompt hook where a shell runs it: everywhere but a
// Windows without Git Bash, where Claude Code falls back to PowerShell
// (ADR 0212).
func TestClaudeCodeHooksIncludePromptNoticesWhereAShellRunsThem(t *testing.T) {
	hp := hooks.Paths{ProjectDir: "/opt/imprint", VenvPython: "/opt/imprint/.venv/bin/python", DataDir: "/opt/imprint/data"}
	find := func(specs []hookSpec) *hookSpec {
		for i := range specs {
			if specs[i].event == "UserPromptSubmit" {
				return &specs[i]
			}
		}
		return nil
	}
	got := find(claudeCodeHooks(hp, true))
	if got == nil || got.command != hooks.PromptNoticesCommand(hp) || got.async || got.timeout != 5 {
		t.Fatalf("prompt hook: %+v", got)
	}
	if find(claudeCodeHooks(hp, false)) != nil {
		t.Fatal("a prompt hook without a shell to run it")
	}
}

func TestSetupHooksOwnsThePromptHookAcrossRelocationAndWithdrawal(t *testing.T) {
	settings := filepath.Join(t.TempDir(), "settings.json")
	if err := os.WriteFile(settings, []byte(`{"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"my-own-hook"}]}]}}`), 0600); err != nil {
		t.Fatal(err)
	}
	read := func() string {
		data, err := os.ReadFile(settings)
		if err != nil {
			t.Fatal(err)
		}
		var decoded map[string]any
		if err := json.Unmarshal(data, &decoded); err != nil {
			t.Fatal(err)
		}
		return string(data)
	}
	writeClaudeCodeHooks(settings, backendPaths{ProjectDir: "/old place", VenvPython: "/old place/python", DataDir: "/data"}, true)
	writeClaudeCodeHooks(settings, backendPaths{ProjectDir: "/new place", VenvPython: "/new place/python", DataDir: "/data"}, true)
	content := read()
	// One notice hook on each prompt and one after each tool call (ADRs 0212, 0240).
	if strings.Count(content, "prompt_notices.sh") != 2 || strings.Count(content, "/new place/imprint/prompt_notices.sh") != 2 ||
		strings.Count(content, "IMPRINT_NOTICE_EVENT=PostToolUse") != 1 || !strings.Contains(content, "my-own-hook") {
		t.Fatalf("relocated notice hooks: %s", content)
	}
	writeClaudeCodeHooks(settings, backendPaths{ProjectDir: "/new place", VenvPython: "/new place/python", DataDir: "/data"}, false)
	content = read()
	if strings.Contains(content, "prompt_notices.sh") || !strings.Contains(content, "my-own-hook") {
		t.Fatalf("withdrawn prompt hook: %s", content)
	}
}
