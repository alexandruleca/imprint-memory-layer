package cmd

import (
	"os"

	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// SetupClaudeDesktop wires the Imprint MCP server into the Anthropic Claude
// Desktop app (the consumer app, not Claude Code). Claude Desktop uses the
// standard ``mcpServers`` key in ``claude_desktop_config.json``:
//
//	macOS:        ~/Library/Application Support/Claude/claude_desktop_config.json
//	Windows:      %APPDATA%\Claude\claude_desktop_config.json
//	Windows (MS Store / MSIX install):
//	              %LOCALAPPDATA%\Packages\Claude_<hash>\LocalCache\Roaming\Claude\claude_desktop_config.json
//	Linux (unofficial): $XDG_CONFIG_HOME/Claude/claude_desktop_config.json
//
// WSL2-aware: when running inside WSL, the Windows-side Claude Desktop is
// still wireable because Windows can launch the Linux venv Python through
// ``wsl.exe``. The emitted MCP spec uses:
//
//	command: "wsl.exe"
//	args:    ["-d", "<distro>", "--", "env",
//	         "PYTHONPATH=<linux path>",
//	         "IMPRINT_DATA_DIR=<linux path>",
//	         "<linux venv python>", "-m", "imprint"]
//
// which launches the same Linux venv Imprint that the CLI uses. No
// cross-filesystem Python install required.
func SetupClaudeDesktop() {
	if marker := platform.ClaudeDesktopInstallMarker(); marker == "" {
		output.Skip("Claude Desktop: unsupported platform (or WSL without a resolvable Windows profile).")
		return
	} else {
		output.Success("Claude Desktop install detected: " + marker)
	}

	cfgPath := platform.ClaudeDesktopConfigPath()
	if cfgPath == "" {
		output.Warn("Claude Desktop config path could not be resolved.")
		return
	}

	setupHostsRan++
	bp := setupBackend()

	spec := buildClaudeDesktopSpec(bp)
	if spec == nil {
		return // buildClaudeDesktopSpec already logged the reason.
	}

	output.Info("Checking Claude Desktop MCP registration at " + cfgPath)
	added, err := jsonutil.EnsureMCPServer(cfgPath, "imprint", spec)
	if err != nil {
		output.Warn("Could not update " + cfgPath + ": " + err.Error())
		return
	}
	if added {
		output.Success("Registered imprint MCP server in " + cfgPath)
	} else {
		output.Skip("imprint MCP server already registered in " + cfgPath)
	}

	output.Success("Usage guidance delivered via MCP handshake — Claude Desktop will call `search` before Read/Grep automatically (no manual instruction paste needed).")

	output.Header("═══ Imprint → Claude Desktop setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + cfgPath)
	output.Info("Next steps:")
	output.Info("  1. Fully quit and relaunch Claude Desktop (tray → Quit, then reopen)")
	output.Info("  2. Verify the 'imprint' server appears in Settings → Developer → MCP")
	output.Info("  3. `imprint ingest <dir>` on the CLI to index your projects")
	output.Info("  4. Auto-sync past conversations: request a data export in claude.ai (Settings → Privacy → Export), then run `imprint learn --desktop`. Re-runs are cheap — already-indexed zips are skipped.")
}

// buildClaudeDesktopSpec returns the MCP server spec appropriate for the
// current host → Claude-Desktop-host bridge. On WSL, it wraps the Linux
// venv Python in ``wsl.exe`` so the Windows-side app can launch it.
// On macOS/Windows/Linux-native it's a direct venv-Python invocation.
func buildClaudeDesktopSpec(bp backendPaths) map[string]any {
	if !platform.IsWSL() {
		return map[string]any{
			"command": bp.VenvPython,
			"args":    []any{"-m", "imprint"},
			"env": map[string]any{
				"PYTHONPATH":       bp.ProjectDir,
				"IMPRINT_DATA_DIR": bp.DataDir,
			},
		}
	}

	distro := os.Getenv("WSL_DISTRO_NAME")
	if distro == "" {
		output.Warn("Could not determine WSL distro (WSL_DISTRO_NAME env var is empty). Skipping Claude Desktop wire-in — open a WSL shell and re-run `imprint setup claude-desktop`.")
		return nil
	}

	// wsl.exe -d <distro> -- env KEY=VAL <python> -m imprint
	// `env` sets the WSL-side environment before execing python. Passing
	// env via `env` keeps us out of the double-quoting swamp a shell wrap
	// would create.
	args := []any{
		"-d", distro,
		"--",
		"env",
		"PYTHONPATH=" + bp.ProjectDir,
		"IMPRINT_DATA_DIR=" + bp.DataDir,
		bp.VenvPython,
		"-m", "imprint",
	}
	return map[string]any{
		"command": "wsl.exe",
		"args":    args,
	}
}
