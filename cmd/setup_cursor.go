package cmd

import (
	"os"
	"path/filepath"

	"github.com/hunter/imprint/internal/hooks"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// SetupCursor wires the Imprint MCP server, rule and hooks into Cursor's
// ~/.cursor, which the Cursor app and Cursor CLI both read, on Windows, macOS
// and Linux; Cursor CLI creates the folder too. The hooks (ADR 0236):
// sessionStart, preCompact, postToolUse after an Imprint search (the
// sentinel) and after Read|Grep (the read reminder, ADR 0142), and stop and
// sessionEnd, which learn the conversation from the transcript Cursor names.
// Cursor also runs Claude Code's hooks; outside Windows Imprint's commands
// for both are the same, so Cursor drops Claude Code's copies, and the hook
// lets one of two answer an event where they differ.
func SetupCursor(backend func() backendPaths) {
	cursorDir := platform.CursorConfigDir()
	if !platform.DirExists(cursorDir) {
		output.Warn("Cursor config dir not found: " + cursorDir + " — install Cursor or Cursor CLI first (https://cursor.com). Skipping.")
		return
	}
	output.Success("Cursor config dir found: " + cursorDir)
	setupHostsRan++

	bp := backend()

	// Step: register MCP server globally in ~/.cursor/mcp.json.
	mcpPath := platform.CursorMCPPath()
	output.Info("Checking Cursor MCP registration...")
	spec := map[string]any{
		"command": bp.VenvPython,
		"args":    []any{"-m", "imprint"},
		"env": map[string]any{
			"PYTHONPATH":       bp.ProjectDir,
			"IMPRINT_DATA_DIR": bp.DataDir,
		},
	}
	added, err := jsonutil.EnsureMCPServer(mcpPath, "imprint", spec)
	if err != nil {
		output.Fail("Could not update " + mcpPath + ": " + err.Error())
	} else if added {
		output.Success("Registered imprint MCP server in " + mcpPath)
	} else {
		output.Skip("imprint MCP server already registered in " + mcpPath)
	}

	// Step: install the always-on usage rule.
	rulesDir := platform.CursorRulesDir()
	if !platform.DirExists(rulesDir) {
		if err := os.MkdirAll(rulesDir, 0755); err != nil {
			output.Fail("Could not create " + rulesDir + ": " + err.Error())
		}
	}
	rulePath := filepath.Join(rulesDir, "imprint.mdc")
	output.Info("Checking Cursor rule...")
	rule := rulesFor("cursor", bp.DataDir)
	if existing, err := os.ReadFile(rulePath); err == nil && string(existing) == rule {
		output.Skip("Cursor rule already up to date at " + rulePath)
	} else {
		if err := os.WriteFile(rulePath, []byte(rule), 0644); err != nil {
			output.Fail("Could not write " + rulePath + ": " + err.Error())
		} else {
			output.Success("Wrote Cursor rule to " + rulePath)
		}
	}

	// Step: wire hooks. Cursor's hooks.json schema: {version:1, hooks:{event:[{command, matcher?}]}}.
	// MCP-tool matchers use "MCP:<tool_name>" syntax; we match on the unqualified tool
	// name (`search`) since Cursor strips the server prefix.
	hooksPath := platform.CursorHooksPath()
	output.Info("Checking Cursor hooks...")
	hp := hooks.Paths{ProjectDir: bp.ProjectDir, VenvPython: bp.VenvPython, DataDir: bp.DataDir}
	cursorHooks := cursorHookSet(hp)
	// The read gate that refused reads (exit 2) is gone; drop its preToolUse entry.
	if removed, err := jsonutil.RemoveOwnedCursorHooks(hooksPath, "preToolUse"); err != nil {
		output.Fail("Could not remove the old preToolUse hook: " + err.Error())
	} else if removed > 0 {
		output.Success("Removed the old preToolUse read gate")
	}
	hooksOK := 0
	for _, h := range cursorHooks {
		if err := jsonutil.SetOwnedCursorHook(hooksPath, h.event, h.matcher, h.command); err != nil {
			output.Fail("Could not set " + h.event + " hook: " + err.Error())
		} else {
			label := h.event
			if h.matcher != "" {
				label += "(" + h.matcher + ")"
			}
			output.Success("Configured " + label + " hook")
			hooksOK++
		}
	}

	output.Header("═══ Imprint → Cursor setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + mcpPath)
	output.Info("Rule:       " + rulePath)
	if hooksOK > 0 {
		output.Info("Hooks:      " + hooksPath)
	}
	output.Info("Next steps:")
	output.Info("  1. Restart Cursor to pick up the new MCP server + hooks")
	output.Info("  2. In Cursor settings, verify the 'imprint' MCP server is listed")
	output.Info("  3. Use 'imprint ingest <dir>' to index your project directories")
}

type cursorHook struct{ event, matcher, command string }

// cursorHookSet is what Imprint registers in Cursor's hooks.json (ADR 0236).
// Cursor's preCompact is observational (it cannot block), so the hook nudges
// the user to save context; stop fires after each interactive turn and
// sessionEnd when a session ends, print mode included.
func cursorHookSet(hp hooks.Paths) []cursorHook {
	return []cursorHook{
		{"sessionStart", "", hooks.CursorSessionStartCommand(hp)},
		{"preCompact", "", hooks.CursorPreCompactCommand(hp)},
		{"postToolUse", "MCP:search", hooks.CursorPostSearchCommand(hp)},
		// A read before any Imprint search gets one reminder, never a refusal (ADR 0142).
		{"postToolUse", "Read|Grep", hooks.CursorPostReadCommand(hp)},
		{"stop", "", hooks.CursorStopCommand(hp)},
		{"sessionEnd", "", hooks.CursorStopCommand(hp)},
	}
}
