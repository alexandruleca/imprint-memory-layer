package cmd

import (
	"path/filepath"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
	"github.com/hunter/imprint/internal/tomlutil"
)

// SetupCodex wires the Imprint MCP server into the OpenAI Codex CLI by
// upserting a `[mcp_servers.imprint]` section in ~/.codex/config.toml.
// Codex has no hook system, so enforcement is text-only — wire instructions
// later via an AGENTS.md entry (out of scope this pass).
func SetupCodex() {
	codexPath := platform.CodexConfigPath()
	codexDir := filepath.Dir(codexPath)
	if !platform.DirExists(codexDir) {
		output.Warn("Codex config dir not found: " + codexDir + " — install the Codex CLI first (https://developers.openai.com/codex). Skipping.")
		return
	}
	output.Success("Codex config dir found: " + codexDir)
	setupHostsRan++

	bp := setupBackend()

	output.Info("Checking Codex MCP registration...")
	spec := map[string]any{
		"command": bp.VenvPython,
		"args":    []any{"-m", "imprint"},
		"env": map[string]any{
			"PYTHONPATH":       bp.ProjectDir,
			"IMPRINT_DATA_DIR": bp.DataDir,
		},
	}
	added, err := tomlutil.EnsureMCPServer(codexPath, "imprint", spec)
	if err != nil {
		output.Warn("Could not update " + codexPath + ": " + err.Error())
	} else if added {
		output.Success("Registered imprint MCP server in " + codexPath)
	} else {
		output.Skip("imprint MCP server already registered in " + codexPath)
	}

	output.Header("═══ Imprint → Codex setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + codexPath)
	output.Warn("Codex has no hook system — enforcement is text-only. Add guidance to AGENTS.md if you want the model to check memory first.")
	output.Info("Next steps:")
	output.Info("  1. Restart any running codex session to pick up the new MCP server")
	output.Info("  2. In codex, verify the 'imprint' MCP server is listed")
	output.Info("  3. Use 'imprint ingest <dir>' to index your project directories")
}
