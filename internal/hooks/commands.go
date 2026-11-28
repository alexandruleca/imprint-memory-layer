// Package hooks builds portable commands invoking a Python hook entry point.
package hooks

import (
	"path/filepath"
	"runtime"
	"strings"
)

type Paths struct {
	ProjectDir string
	VenvPython string
	DataDir    string
}

func quote(value string) string {
	if runtime.GOOS == "windows" {
		return `"` + strings.ReplaceAll(value, `"`, `\"`) + `"`
	}
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

func command(bp Paths, action string) string {
	return quote(bp.VenvPython) + " " + quote(filepath.Join(bp.ProjectDir, "imprint", "hook_entry.py")) +
		" " + action + " " + quote(bp.DataDir)
}

// powershellCommand is a hook command for a client that runs command hooks
// in PowerShell on Windows and in sh or bash elsewhere: GitHub Copilot, and
// Cursor, which pipes the payload into the command inside a PowerShell
// script block (ADR 0236). PowerShell needs the call operator to run a quoted
// path; elsewhere the command is the one every other client gets.
func powershellCommand(bp Paths, action string) string {
	if runtime.GOOS != "windows" {
		return command(bp, action)
	}
	quote := func(value string) string { return "'" + strings.ReplaceAll(value, "'", "''") + "'" }
	return "& " + quote(bp.VenvPython) + " " + quote(filepath.Join(bp.ProjectDir, "imprint", "hook_entry.py")) +
		" " + action + " " + quote(bp.DataDir)
}

// Copilot reads a SessionStart hook's context from top-level
// additionalContext; its Stop payload is Claude Code's (ADR 0057).
func CopilotSessionStartCommand(bp Paths) string {
	return powershellCommand(bp, "copilot-session-start")
}
func CopilotStopCommand(bp Paths) string { return powershellCommand(bp, "stop") }

func StopCommand(bp Paths) string               { return command(bp, "stop") }
func PreCompactCommand(bp Paths) string         { return command(bp, "pre-compact") }
func SessionStartCommand(bp Paths) string       { return command(bp, "session-start") }
func PostSearchSentinelCommand(bp Paths) string { return command(bp, "post-search") }
func PreReadGateCommand(bp Paths) string        { return command(bp, "pre-read") }

// Cursor's hooks (ADR 0236). Outside Windows each is byte for byte the
// command Claude Code gets for the same action, so Cursor, which also runs
// Claude Code's hooks, drops Claude Code's copy as a duplicate. The hook
// answers in Cursor's shape when the payload is Cursor's.
func CursorSessionStartCommand(bp Paths) string { return powershellCommand(bp, "session-start") }
func CursorPreCompactCommand(bp Paths) string   { return powershellCommand(bp, "pre-compact") }
func CursorPostSearchCommand(bp Paths) string   { return powershellCommand(bp, "post-search") }
func CursorStopCommand(bp Paths) string         { return powershellCommand(bp, "stop") }

// CursorPostReadCommand reminds a Cursor session that reads before searching
// Imprint, after the read (ADR 0142): Cursor's preToolUse can only allow or deny.
func CursorPostReadCommand(bp Paths) string { return powershellCommand(bp, "cursor-post-read") }

// PromptNoticesCommand prints, on each prompt, what the conversation's
// Imprint server prepared for it (ADR 0212): a shell script sourced in the
// hook's own shell (sh, or Git Bash on Windows) rather than Python, so a
// prompt starts no other process when nothing waits. Forward slashes keep
// Git Bash from searching PATH for a Windows path.
func PromptNoticesCommand(bp Paths) string {
	return "IMPRINT_PROMPT_DATA=" + quote(filepath.ToSlash(bp.DataDir)) + " . " +
		quote(filepath.ToSlash(filepath.Join(bp.ProjectDir, "imprint", "prompt_notices.sh")))
}

// ToolNoticesCommand prints the same notice after each tool call (ADR
// 0240), so a working agent hears the person's comment mid-turn: the
// prompt hook's script, answering as a PostToolUse hook.
func ToolNoticesCommand(bp Paths) string {
	return "IMPRINT_PROMPT_DATA=" + quote(filepath.ToSlash(bp.DataDir)) + " IMPRINT_NOTICE_EVENT=PostToolUse . " +
		quote(filepath.ToSlash(filepath.Join(bp.ProjectDir, "imprint", "prompt_notices.sh")))
}
