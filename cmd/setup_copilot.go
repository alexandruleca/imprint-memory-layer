package cmd

import (
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// SetupCopilot wires Imprint into GitHub Copilot's agent mode via VSCode's
// user-global mcp.json. Uses the `servers` root key (distinct from Cursor
// and Cline, which use `mcpServers`). Copilot has no hook system — the
// agent decides when to call tools.
func SetupCopilot() {
	userDir := platform.VSCodeUserDir()
	if userDir == "" {
		output.Warn("VSCode user directory not found (no Code or Code - Insiders install detected) — install VSCode with GitHub Copilot first. Skipping.")
		return
	}
	output.Success("VSCode user directory found: " + userDir)
	setupHostsRan++

	bp := setupBackend()

	mcpPath := platform.CopilotMCPPath()
	output.Info("Checking Copilot MCP registration...")
	spec := map[string]any{
		"command": bp.VenvPython,
		"args":    []any{"-m", "imprint"},
		"env": map[string]any{
			"PYTHONPATH":       bp.ProjectDir,
			"IMPRINT_DATA_DIR": bp.DataDir,
		},
	}
	added, err := jsonutil.EnsureMCPServerAtKey(mcpPath, "servers", "imprint", spec)
	if err != nil {
		output.Warn("Could not update " + mcpPath + ": " + err.Error())
	} else if added {
		output.Success("Registered imprint MCP server in " + mcpPath)
	} else {
		output.Skip("imprint MCP server already registered in " + mcpPath)
	}

	output.Header("═══ Imprint → GitHub Copilot setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + mcpPath)
	output.Warn("Copilot has no hook system — enforcement is text-only. Add guidance to .github/copilot-instructions.md if you want the agent to check memory first.")
	output.Info("Next steps:")
	output.Info("  1. Reload VSCode (Ctrl+Shift+P → 'Developer: Reload Window')")
	output.Info("  2. Open Copilot Chat in Agent mode and confirm the imprint tools are listed")
	output.Info("  3. Use 'imprint ingest <dir>' to index your project directories")
}
