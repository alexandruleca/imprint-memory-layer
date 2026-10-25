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

// copilotCommand is a hook command for the shell GitHub Copilot runs a
// command hook in: PowerShell on Windows, which needs the call operator to
// run a quoted path, and bash elsewhere.
func copilotCommand(bp Paths, action string) string {
	if runtime.GOOS != "windows" {
		return command(bp, action)
	}
	quote := func(value string) string { return "'" + strings.ReplaceAll(value, "'", "''") + "'" }
	return "& " + quote(bp.VenvPython) + " " + quote(filepath.Join(bp.ProjectDir, "imprint", "hook_entry.py")) +
		" " + action + " " + quote(bp.DataDir)
}

// Copilot reads a SessionStart hook's context from top-level
// additionalContext; its Stop payload is Claude Code's (ADR 0057).
func CopilotSessionStartCommand(bp Paths) string { return copilotCommand(bp, "copilot-session-start") }
func CopilotStopCommand(bp Paths) string         { return copilotCommand(bp, "stop") }

func StopCommand(bp Paths) string               { return command(bp, "stop") }
func PreCompactCommand(bp Paths) string         { return command(bp, "pre-compact") }
func CursorPreCompactCommand(bp Paths) string   { return command(bp, "cursor-pre-compact") }
func SessionStartCommand(bp Paths) string       { return command(bp, "session-start") }
func PostSearchSentinelCommand(bp Paths) string { return command(bp, "post-search") }
func PreReadGateCommand(bp Paths) string        { return command(bp, "pre-read") }
