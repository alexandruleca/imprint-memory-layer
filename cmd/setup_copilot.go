package cmd

import (
	"os"
	"path/filepath"

	"github.com/hunter/imprint/internal/instructions"
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

	// Step: write user-scope Copilot custom instructions with managed markers
	// so user-authored content in the same file is preserved.
	rulePath := platform.CopilotInstructionsPath()
	wroteRule := false
	if rulePath != "" {
		output.Info("Checking Copilot instructions...")
		if err := os.MkdirAll(filepath.Dir(rulePath), 0755); err != nil {
			output.Warn("Could not create " + filepath.Dir(rulePath) + ": " + err.Error())
		}
		existing := ""
		if data, err := os.ReadFile(rulePath); err == nil {
			existing = string(data)
		}
		managed := instructions.MarkerStart + "\n" + instructions.CopilotRule + instructions.MarkerEnd + "\n"
		updated := instructions.MergeManaged(existing, managed)
		if updated == existing {
			output.Skip("Copilot instructions already up to date at " + rulePath)
			wroteRule = true
		} else if err := os.WriteFile(rulePath, []byte(updated), 0644); err != nil {
			output.Warn("Could not write " + rulePath + ": " + err.Error())
		} else {
			output.Success("Wrote Copilot instructions to " + rulePath)
			wroteRule = true
		}
	}

	output.Header("═══ Imprint → GitHub Copilot setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + mcpPath)
	if wroteRule {
		output.Info("Rule:       " + rulePath)
	}
	output.Warn("Copilot has no hook system — enforcement is advisory via the rule file above. Session summarizer cannot auto-run.")
	output.Info("Next steps:")
	output.Info("  1. Reload VSCode (Ctrl+Shift+P → 'Developer: Reload Window')")
	output.Info("  2. Open Copilot Chat in Agent mode and confirm the imprint tools are listed")
	output.Info("  3. Use 'imprint ingest <dir>' to index your project directories")
}
