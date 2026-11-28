package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/hunter/imprint/internal/hooks"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/platform"
)

// Cursor's hooks (ADR 0236): the same set on every platform, written beside
// the user's own, replacing what an earlier release wrote, unchanged by a
// second setup. Outside Windows each command is the one Claude Code gets for
// the same event, so Cursor, which also runs Claude Code's hooks, drops
// Claude Code's copies.
func TestCursorSetupWritesItsHooksBesideTheUsersOwn(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("IMPRINT_INSTRUCTIONS_FULL_RULES", "")
	project := filepath.Join(t.TempDir(), "runtime")
	bp := backendPaths{ProjectDir: project, VenvPython: filepath.Join(project, ".venv", "bin", "python"), DataDir: filepath.Join(project, "data")}
	hp := hooks.Paths{ProjectDir: bp.ProjectDir, VenvPython: bp.VenvPython, DataDir: bp.DataDir}
	hooksFile := platform.CursorHooksPath()
	earlier := func(action string) string {
		return "'" + bp.VenvPython + "' '" + filepath.Join(project, "imprint", "hook_entry.py") + "' " + action + " '" + bp.DataDir + "'"
	}
	fixture, _ := json.Marshal(map[string]any{"version": 1, "hooks": map[string]any{
		"stop":       []any{map[string]any{"command": "./mine.sh"}},
		"preCompact": []any{map[string]any{"command": earlier("cursor-pre-compact")}},
		"preToolUse": []any{map[string]any{"command": earlier("pre-read"), "matcher": "Read|Grep"}},
	}})
	writeCleanupFixture(t, hooksFile, string(fixture))

	SetupCursor(func() backendPaths { return bp })
	first, err := os.ReadFile(hooksFile)
	if err != nil {
		t.Fatal(err)
	}
	SetupCursor(func() backendPaths { return bp })
	if again, _ := os.ReadFile(hooksFile); string(again) != string(first) {
		t.Fatalf("a second setup changed hooks.json:\n%s\n%s", first, again)
	}
	data, err := jsonutil.ReadJSON(hooksFile)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string][]map[string]any{
		"sessionStart": {{"command": hooks.CursorSessionStartCommand(hp)}},
		"preCompact":   {{"command": hooks.CursorPreCompactCommand(hp)}},
		"postToolUse": {{"command": hooks.CursorPostSearchCommand(hp), "matcher": "MCP:search"},
			{"command": hooks.CursorPostReadCommand(hp), "matcher": "Read|Grep"}},
		"stop":       {{"command": "./mine.sh"}, {"command": hooks.CursorStopCommand(hp)}},
		"sessionEnd": {{"command": hooks.CursorStopCommand(hp)}},
	}
	got, _ := json.Marshal(data["hooks"])
	wanted, _ := json.Marshal(want)
	if string(got) != string(wanted) {
		t.Fatalf("hooks.json = %s\nwant %s", got, wanted)
	}

	if runtime.GOOS == "windows" {
		return // Cursor runs hooks in PowerShell there and Claude Code in Git Bash: the hook tells them apart.
	}
	// Cursor's names for Claude Code's events (its loader's mapping, checked with Cursor CLI 2026.11.22).
	events := map[string]string{"SessionStart": "sessionStart", "PreCompact": "preCompact", "PostToolUse": "postToolUse", "Stop": "stop"}
	for _, spec := range claudeCodeHooks(hp, true) {
		event, ok := events[spec.event]
		if !ok {
			continue
		}
		found := false
		for _, entry := range want[event] {
			found = found || entry["command"] == spec.command
		}
		if !found {
			t.Errorf("Claude Code's %s hook %q has no identical Cursor %s hook, so Cursor would run both", spec.event, spec.command, event)
		}
	}
}
