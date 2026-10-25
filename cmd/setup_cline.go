package cmd

import (
	"os"
	"path/filepath"

	"github.com/hunter/imprint/internal/configfile"
	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// SetupCline registers Imprint with Cline. Since Cline 4 every surface (the
// VS Code extension and its forks, the CLI, JetBrains and the desktop app)
// reads one MCP settings file in Cline's data directory and the rules in its
// global rules directories, so one registration serves them all (ADR 0056).
// Cline keeps its own conversations, which imprint learn reads; its hooks
// receive no transcript, so none are wired.
func SetupCline(backend func() backendPaths) {
	if !clineInstalled() {
		output.Warn("Cline not detected (no " + platform.ClineDir() + " and no Cline extension storage in VS Code). Skipping.")
		return
	}
	setupHostsRan++

	bp := backend()
	settings, rule := platform.ClineMCPSettingsPath(), platform.ClineRulesPath()
	output.Info("Checking Cline MCP registration...")
	added, err := jsonutil.EnsureMCPServer(settings, "imprint", clineServer(bp.VenvPython, []any{"-m", "imprint"}, map[string]any{
		"PYTHONPATH":       bp.ProjectDir,
		"IMPRINT_DATA_DIR": bp.DataDir,
	}))
	if err != nil {
		output.Fail("Could not update " + settings + ": " + err.Error())
	} else if added {
		output.Success("Registered imprint MCP server in " + settings)
	} else {
		output.Skip("imprint MCP server already registered in " + settings)
	}

	output.Info("Checking Cline rule...")
	if existing, err := os.ReadFile(rule); err == nil && string(existing) == instructions.ClineRule {
		output.Skip("Cline rule already up to date at " + rule)
	} else if err := configfile.Write(rule, []byte(instructions.ClineRule)); err != nil {
		output.Fail("Could not write " + rule + ": " + err.Error())
	} else {
		output.Success("Wrote Cline rule to " + rule)
	}

	output.Header("═══ Imprint → Cline setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + settings)
	output.Info("Rule:       " + rule)
	output.Info("Cline's hooks receive no transcript, so no hook is wired: conversations arrive through 'imprint learn' or the dashboard's Learn action.")
	output.Info("Next steps:")
	output.Info("  - Reload the Cline extension, or restart a running cline session, to pick up the new MCP server")
	output.Info("  - Use 'imprint ingest <dir>' to index your project directories")
}

// clineInstalled: Cline's directory exists once any Cline 4 surface has run;
// the VS Code extension's storage exists once VS Code has loaded it.
func clineInstalled() bool {
	if platform.DirExists(platform.ClineDir()) || platform.DirExists(platform.ClineDataDir()) {
		return true
	}
	for _, dir := range platform.VSCodeUserDirs() {
		if platform.DirExists(filepath.Join(dir, "globalStorage", "saoudrizwan.claude-dev")) {
			return true
		}
	}
	return false
}

// clineServer is Imprint's entry in a Cline MCP settings file, in the flat
// stdio form every Cline version reads. Cline reads a nested "transport"
// before the flat fields, and `cline mcp add` writes one, so Imprint's entry
// must have none: the nil removes it and keeps the entry's other settings
// (disabled, timeout).
func clineServer(command string, args []any, env map[string]any) map[string]any {
	return map[string]any{"command": command, "args": args, "env": env, "transport": nil}
}
