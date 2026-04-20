package cmd

import (
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// SetupOpenClaw wires the Imprint MCP server into OpenClaw (aka Clawdbot /
// Moltbot). OpenClaw stores its config in ~/.openclaw/openclaw.json and nests
// MCP servers two levels deep under `mcp.servers` — unlike Cursor/Cline
// (`mcpServers`) or Copilot (`servers`). No hook system is exposed, so this
// target only registers the MCP entry and leaves enforcement to the rule text
// injected by the MCP server itself on SessionStart.
func SetupOpenClaw() {
	openclawDir := platform.OpenClawConfigDir()
	if !platform.DirExists(openclawDir) {
		output.Warn("OpenClaw config dir not found: " + openclawDir + " — install OpenClaw first (https://docs.openclaw.ai/cli). Skipping.")
		return
	}
	output.Success("OpenClaw config dir found: " + openclawDir)
	setupHostsRan++

	bp := setupBackend()

	mcpPath := platform.OpenClawMCPPath()
	output.Info("Checking OpenClaw MCP registration...")
	spec := map[string]any{
		"command": bp.VenvPython,
		"args":    []any{"-m", "imprint"},
		"env": map[string]any{
			"PYTHONPATH":       bp.ProjectDir,
			"IMPRINT_DATA_DIR": bp.DataDir,
		},
	}
	added, err := jsonutil.EnsureMCPServerNested(mcpPath, []string{"mcp", "servers"}, "imprint", spec)
	if err != nil {
		output.Warn("Could not update " + mcpPath + ": " + err.Error())
	} else if added {
		output.Success("Registered imprint MCP server in " + mcpPath)
	} else {
		output.Skip("imprint MCP server already registered in " + mcpPath)
	}

	output.Header("═══ Imprint → OpenClaw setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + mcpPath)
	output.Info("Next steps:")
	output.Info("  1. Restart OpenClaw to pick up the new MCP server")
	output.Info("  2. Run `openclaw mcp list` to confirm imprint is listed")
	output.Info("  3. Use 'imprint ingest <dir>' to index your project directories")
}
