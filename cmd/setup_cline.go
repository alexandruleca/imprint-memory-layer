package cmd

import (
	"path/filepath"

	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// SetupCline wires Imprint into Cline. Two variants are supported: the
// VSCode extension (config lives under VSCode's globalStorage for
// saoudrizwan.claude-dev) and the Cline CLI (~/.cline/data/settings). Each
// is handled independently — if neither is detected, we warn and return.
func SetupCline() {
	extPath := platform.ClineExtSettingsPath()
	cliPath := platform.ClineCLISettingsPath()

	extDir := ""
	if extPath != "" {
		extDir = filepath.Dir(extPath)
	}
	cliDir := filepath.Dir(cliPath)

	extPresent := extPath != "" && platform.DirExists(filepath.Dir(extDir)) // parent of `settings` = extension storage dir
	cliPresent := platform.DirExists(filepath.Dir(cliDir))                  // parent of `settings` = data dir

	if !extPresent && !cliPresent {
		output.Warn("Cline not detected — neither VSCode extension (saoudrizwan.claude-dev) nor Cline CLI (~/.cline) is installed. Skipping.")
		return
	}

	bp := setupBackend()
	spec := map[string]any{
		"command": bp.VenvPython,
		"args":    []any{"-m", "imprint"},
		"env": map[string]any{
			"PYTHONPATH":       bp.ProjectDir,
			"IMPRINT_DATA_DIR": bp.DataDir,
		},
	}

	wroteExt := false
	if extPresent {
		output.Info("Checking Cline (VSCode extension) MCP registration...")
		added, err := jsonutil.EnsureMCPServer(extPath, "imprint", spec)
		if err != nil {
			output.Warn("Could not update " + extPath + ": " + err.Error())
		} else if added {
			output.Success("Registered imprint MCP server in " + extPath)
			wroteExt = true
		} else {
			output.Skip("imprint MCP server already registered in " + extPath)
			wroteExt = true
		}
	} else {
		output.Skip("Cline VSCode extension not detected")
	}

	wroteCLI := false
	if cliPresent {
		output.Info("Checking Cline (CLI) MCP registration...")
		added, err := jsonutil.EnsureMCPServer(cliPath, "imprint", spec)
		if err != nil {
			output.Warn("Could not update " + cliPath + ": " + err.Error())
		} else if added {
			output.Success("Registered imprint MCP server in " + cliPath)
			wroteCLI = true
		} else {
			output.Skip("imprint MCP server already registered in " + cliPath)
			wroteCLI = true
		}
	} else {
		output.Skip("Cline CLI not detected")
	}

	output.Header("═══ Imprint → Cline setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	if wroteExt {
		output.Info("Extension:  " + extPath)
	}
	if wroteCLI {
		output.Info("CLI:        " + cliPath)
	}
	output.Warn("Cline has no hook system — enforcement is text-only. Add guidance to .clinerules if you want the agent to check memory first.")
	output.Info("Next steps:")
	if wroteExt {
		output.Info("  - Reload VSCode to pick up the extension's new MCP server")
	}
	if wroteCLI {
		output.Info("  - Restart any running cline session to pick up the new MCP server")
	}
	output.Info("  - Use 'imprint ingest <dir>' to index your project directories")
}
