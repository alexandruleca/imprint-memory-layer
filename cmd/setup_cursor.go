package cmd

import (
	"os"
	"path/filepath"

	"github.com/hunter/knowledge/internal/instructions"
	"github.com/hunter/knowledge/internal/jsonutil"
	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
	"github.com/hunter/knowledge/internal/runner"
)

// SetupCursor wires the Knowledge MCP server into Cursor. Cursor has no hook
// system, so enforcement is text-only via an always-applied rule. The MCP
// server itself is registered globally in ~/.cursor/mcp.json so tool calls
// work the same way as in Claude Code.
func SetupCursor() {
	cursorDir := platform.CursorConfigDir()
	if !platform.DirExists(cursorDir) {
		output.Fail("Cursor config dir not found: " + cursorDir + " — install Cursor first (https://cursor.sh).")
	}
	output.Success("Cursor config dir found: " + cursorDir)

	bp := setupBackend()

	// Step: register MCP server globally in ~/.cursor/mcp.json.
	mcpPath := platform.CursorMCPPath()
	output.Info("Checking Cursor MCP registration...")
	spec := map[string]any{
		"command": bp.VenvPython,
		"args":    []any{"-m", "knowledgebase"},
		"env": map[string]any{
			"PYTHONPATH":         bp.ProjectDir,
			"KNOWLEDGE_DATA_DIR": bp.DataDir,
		},
	}
	added, err := jsonutil.EnsureMCPServer(mcpPath, "knowledge", spec)
	if err != nil {
		output.Warn("Could not update " + mcpPath + ": " + err.Error())
	} else if added {
		output.Success("Registered knowledge MCP server in " + mcpPath)
	} else {
		output.Skip("knowledge MCP server already registered in " + mcpPath)
	}

	// Step: install the always-on usage rule.
	rulesDir := platform.CursorRulesDir()
	if !platform.DirExists(rulesDir) {
		if err := os.MkdirAll(rulesDir, 0755); err != nil {
			output.Warn("Could not create " + rulesDir + ": " + err.Error())
		}
	}
	rulePath := filepath.Join(rulesDir, "knowledge.mdc")
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

	output.Header("═══ Knowledge → Cursor setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + mcpPath)
	output.Info("Rule:       " + rulePath)
	output.Warn("Cursor has no hook system — enforcement is text-only via the always-on rule. For hard enforcement (PreToolUse block) use Claude Code.")
	output.Info("Next steps:")
	output.Info("  1. Restart Cursor to pick up the new MCP server")
	output.Info("  2. In Cursor settings, verify the 'knowledge' MCP server is listed")
	output.Info("  3. Use 'knowledge ingest <dir>' to index your project directories")
}
