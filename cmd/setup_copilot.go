package cmd

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/hunter/imprint/internal/configfile"
	"github.com/hunter/imprint/internal/hooks"
	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// SetupCopilot registers Imprint with GitHub Copilot. VS Code's Copilot
// harness (the default since VS Code 1.143), the Copilot CLI and JetBrains
// share the Copilot home (COPILOT_HOME, else ~/.copilot): its MCP servers in
// mcp-config.json, its always-on instructions in copilot-instructions.md and
// its hooks in hooks/*.json. VS Code's Local harness discovers the same MCP
// file (ADR 0057).
func SetupCopilot(backend func() backendPaths) {
	if !copilotInstalled() {
		output.Warn("GitHub Copilot not detected (no " + platform.CopilotHome() + ", no copilot command and no VS Code). Skipping.")
		return
	}
	setupHostsRan++

	bp := backend()
	home := platform.CopilotHome()
	registry := filepath.Join(home, "mcp-config.json")
	output.Info("Checking Copilot MCP registration...")
	added, err := jsonutil.EnsureMCPServer(registry, "imprint", copilotServer(bp.VenvPython, []any{"-m", "imprint"}, map[string]any{
		"PYTHONPATH":       bp.ProjectDir,
		"IMPRINT_DATA_DIR": bp.DataDir,
	}))
	if err != nil {
		output.Fail("Could not update " + registry + ": " + err.Error())
	} else if added {
		output.Success("Registered imprint MCP server in " + registry)
	} else {
		output.Skip("imprint MCP server already registered in " + registry)
	}

	rules := filepath.Join(home, "copilot-instructions.md")
	output.Info("Checking Copilot instructions...")
	if changed, err := mergeManagedRule(rules, instructions.CopilotRule); err != nil {
		output.Fail("Could not write " + rules + ": " + err.Error())
	} else if changed {
		output.Success("Wrote Copilot instructions to " + rules)
	} else {
		output.Skip("Copilot instructions already up to date at " + rules)
	}

	hookFile := filepath.Join(home, "hooks", "imprint.json")
	hp := hooks.Paths{ProjectDir: bp.ProjectDir, VenvPython: bp.VenvPython, DataDir: bp.DataDir}
	output.Info("Checking Copilot hooks...")
	if err := configfile.Write(hookFile, copilotHooks(hooks.CopilotSessionStartCommand(hp), hooks.CopilotStopCommand(hp))); err != nil {
		output.Fail("Could not write " + hookFile + ": " + err.Error())
	}
	output.Success("Configured SessionStart and Stop hooks in " + hookFile)

	output.Header("═══ Imprint → GitHub Copilot setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + registry)
	output.Info("Rule:       " + rules)
	output.Info("Hooks:      " + hookFile)
	output.Info("Next steps:")
	output.Info("  1. Reload VS Code (Developer: Reload Window) or restart the copilot CLI")
	output.Info("  2. Confirm the imprint tools are listed in Copilot's agent tools")
	output.Info("  3. Use 'imprint ingest <dir>' to index your project directories")
}

// copilotInstalled: the Copilot home exists once any Copilot surface has
// run; a CLI-only install has the copilot command, and VS Code bundles
// Copilot.
func copilotInstalled() bool {
	if platform.DirExists(platform.CopilotHome()) {
		return true
	}
	if _, err := exec.LookPath("copilot"); err == nil {
		return true
	}
	for _, dir := range platform.VSCodeUserDirs() {
		if platform.DirExists(dir) {
			return true
		}
	}
	return false
}

// copilotServer is Imprint's entry in a Copilot mcp-config.json.
func copilotServer(command string, args []any, env map[string]any) map[string]any {
	return map[string]any{"type": "stdio", "command": command, "args": args, "env": env, "tools": []any{"*"}}
}

// copilotHooks is the hooks file Imprint owns in a Copilot home. Events named
// in PascalCase receive Claude Code's payloads (session_id, cwd and, for
// Stop, transcript_path), which hook_entry reads.
func copilotHooks(sessionStart, stop string) []byte {
	var data bytes.Buffer
	encoder := json.NewEncoder(&data)
	encoder.SetEscapeHTML(false) // PowerShell's call operator stays readable
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{"version": 1, "hooks": map[string]any{
		"SessionStart": []any{map[string]any{"type": "command", "command": sessionStart, "timeoutSec": 10}},
		"Stop":         []any{map[string]any{"type": "command", "command": stop, "timeoutSec": 120}},
	}})
	return data.Bytes()
}

// mergeManagedRule writes rule as the managed Imprint block of file, keeping
// the rest of the file. It reports whether the file changed.
func mergeManagedRule(file, rule string) (bool, error) {
	existing, err := os.ReadFile(file)
	if err != nil && !os.IsNotExist(err) {
		return false, err
	}
	updated := instructions.MergeManaged(string(existing), instructions.MarkerStart+"\n"+rule+instructions.MarkerEnd+"\n")
	if updated == string(existing) {
		return false, nil
	}
	return true, configfile.Write(file, []byte(updated))
}
