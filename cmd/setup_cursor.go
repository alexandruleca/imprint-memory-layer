package cmd

import (
	"os"
	"path/filepath"

	"github.com/hunter/imprint/internal/hooks"
	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// SetupCursor wires the Imprint MCP server into Cursor. Cursor exposes a
// hook system (version 1, ~/.cursor/hooks.json) as of 2026-04, so this
// target gets parity with Claude Code wherever Cursor's events line up:
// sessionStart, preCompact, preToolUse (Read|Grep gate), and postToolUse
// (mcp__imprint__search sentinel). Stop/sessionEnd is skipped because
// Cursor's payload omits transcript_path — the summarizer has nothing
// to read.
func SetupCursor() {
	cursorDir := platform.CursorConfigDir()
	if !platform.DirExists(cursorDir) {
		output.Warn("Cursor config dir not found: " + cursorDir + " — install Cursor first (https://cursor.sh). Skipping.")
		return
	}
	output.Success("Cursor config dir found: " + cursorDir)
	setupHostsRan++

	bp := setupBackend()

	// Step: register MCP server globally in ~/.cursor/mcp.json.
	mcpPath := platform.CursorMCPPath()
	output.Info("Checking Cursor MCP registration...")
	spec := map[string]any{
		"command": bp.VenvPython,
		"args":    []any{"-m", "imprint"},
		"env": map[string]any{
			"PYTHONPATH":         bp.ProjectDir,
			"IMPRINT_DATA_DIR": bp.DataDir,
		},
	}
	added, err := jsonutil.EnsureMCPServer(mcpPath, "imprint", spec)
	if err != nil {
		output.Warn("Could not update " + mcpPath + ": " + err.Error())
	} else if added {
		output.Success("Registered imprint MCP server in " + mcpPath)
	} else {
		output.Skip("imprint MCP server already registered in " + mcpPath)
	}

	// Step: install the always-on usage rule.
	rulesDir := platform.CursorRulesDir()
	if !platform.DirExists(rulesDir) {
		if err := os.MkdirAll(rulesDir, 0755); err != nil {
			output.Warn("Could not create " + rulesDir + ": " + err.Error())
		}
	}
	rulePath := filepath.Join(rulesDir, "imprint.mdc")
	output.Info("Checking Cursor rule...")
	if existing, err := os.ReadFile(rulePath); err == nil && string(existing) == instructions.CursorRule {
		output.Skip("Cursor rule already up to date at " + rulePath)
	} else {
		if err := os.WriteFile(rulePath, []byte(instructions.CursorRule), 0644); err != nil {
			output.Warn("Could not write " + rulePath + ": " + err.Error())
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
	type cursorHook struct{ event, matcher, command string }
	cursorHooks := []cursorHook{
		{"sessionStart", "", hooks.SessionStartCommand()},
		// Cursor's preCompact is observational (can't block); we emit
		// `user_message` as a save-context nudge to the user.
		{"preCompact", "", hooks.CursorPreCompactCommand()},
		{"postToolUse", "MCP:search", hooks.PostSearchSentinelCommand(hp)},
		{"preToolUse", "Read|Grep", hooks.PreReadGateCommand(hp)},
	}
	hooksOK := 0
	for _, h := range cursorHooks {
		if err := jsonutil.SetCursorHook(hooksPath, h.event, h.matcher, h.command); err != nil {
			output.Warn("Could not set " + h.event + " hook: " + err.Error())
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
