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

func StopCommand(bp Paths) string               { return command(bp, "stop") }
func PreCompactCommand(bp Paths) string         { return command(bp, "pre-compact") }
func CursorPreCompactCommand(bp Paths) string   { return command(bp, "cursor-pre-compact") }
func SessionStartCommand(bp Paths) string       { return command(bp, "session-start") }
func PostSearchSentinelCommand(bp Paths) string { return command(bp, "post-search") }
func PreReadGateCommand(bp Paths) string        { return command(bp, "pre-read") }
