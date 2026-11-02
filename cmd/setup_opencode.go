package cmd

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"slices"

	"github.com/hunter/imprint/internal/configfile"
	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// SetupOpenCode registers Imprint with OpenCode (ADR 0124): a local MCP
// server under "mcp" in the global opencode.json, Imprint's instructions file
// listed in its "instructions", and a plugin that learns a session when it
// goes idle, as the Stop hook does for Claude Code. Its rules are not written
// into OpenCode's AGENTS.md: creating that file would stop OpenCode reading
// ~/.claude/CLAUDE.md in its place.
func SetupOpenCode(backend func() backendPaths) {
	if !openCodeInstalled() {
		output.Warn("OpenCode not detected (no " + platform.OpenCodeConfigDir() + ", no " + platform.OpenCodeDataDir() + " and no opencode command). Skipping.")
		return
	}
	setupHostsRan++

	bp := backend()
	config, rule, plugin := platform.OpenCodeConfigPath(), platform.OpenCodeRulePath(), platform.OpenCodePluginPath()

	output.Info("Checking OpenCode MCP registration...")
	added, err := jsonutil.EnsureMCPServerNested(config, []string{"mcp"}, "imprint", openCodeServer(bp))
	if err != nil {
		output.Fail("Could not update " + config + ": " + err.Error())
	} else if added {
		output.Success("Registered imprint MCP server in " + config)
	} else {
		output.Skip("imprint MCP server already registered in " + config)
	}

	output.Info("Checking OpenCode instructions...")
	if existing, err := os.ReadFile(rule); err == nil && string(existing) == instructions.OpenCodeRule {
		output.Skip("OpenCode instructions already up to date at " + rule)
	} else if err := configfile.Write(rule, []byte(instructions.OpenCodeRule)); err != nil {
		output.Fail("Could not write " + rule + ": " + err.Error())
	} else {
		output.Success("Wrote OpenCode instructions to " + rule)
	}
	if listed, err := ensureOpenCodeInstruction(config, rule); err != nil {
		output.Fail("Could not list " + rule + " in " + config + ": " + err.Error())
	} else if listed {
		output.Success("Listed " + rule + " in " + config + " instructions")
	}

	output.Info("Checking OpenCode plugin...")
	script := openCodePlugin(bp)
	if existing, err := os.ReadFile(plugin); err == nil && string(existing) == script {
		output.Skip("OpenCode plugin already up to date at " + plugin)
	} else if err := configfile.Write(plugin, []byte(script)); err != nil {
		output.Fail("Could not write " + plugin + ": " + err.Error())
	} else {
		output.Success("Wrote OpenCode plugin to " + plugin)
	}

	output.Header("═══ Imprint → OpenCode setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + config)
	output.Info("Rule:       " + rule)
	output.Info("Plugin:     " + plugin + " (learns a session when it goes idle)")
	output.Info("Next steps:")
	output.Info("  1. Restart any running opencode session to pick up the MCP server and plugin")
	output.Info("  2. Run 'opencode mcp list' to confirm imprint is listed")
	output.Info("  3. Use 'imprint learn' to learn existing OpenCode sessions")
}

// openCodeInstalled: OpenCode's config or data directory exists once it has
// run; the command exists once it is installed.
func openCodeInstalled() bool {
	if platform.DirExists(platform.OpenCodeConfigDir()) || platform.DirExists(platform.OpenCodeDataDir()) {
		return true
	}
	_, err := exec.LookPath("opencode")
	return err == nil
}

// openCodeServer is Imprint's entry in OpenCode's "mcp" table: a local server
// whose command is one array, with its environment under "environment".
func openCodeServer(bp backendPaths) map[string]any {
	return map[string]any{
		"type":    "local",
		"command": []any{bp.VenvPython, "-m", "imprint"},
		"environment": map[string]any{
			"PYTHONPATH":       bp.ProjectDir,
			"IMPRINT_DATA_DIR": bp.DataDir,
		},
		"enabled": true,
	}
}

// ensureOpenCodeInstruction lists path in config's "instructions" array,
// keeping the user's entries. It reports whether it changed the file.
func ensureOpenCodeInstruction(config, path string) (bool, error) {
	doc, err := jsonutil.ReadJSON(config)
	if os.IsNotExist(err) {
		doc = map[string]any{}
	} else if err != nil {
		return false, err
	}
	list, _ := doc["instructions"].([]any)
	if slices.Contains(list, any(path)) {
		return false, nil
	}
	doc["instructions"] = append(list, path)
	return true, jsonutil.WriteJSON(config, doc)
}

// removeOpenCodeInstruction drops path from config's "instructions" array.
func removeOpenCodeInstruction(config, path string) error {
	doc, err := jsonutil.ReadJSON(config)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	list, _ := doc["instructions"].([]any)
	kept := slices.DeleteFunc(slices.Clone(list), func(entry any) bool { return entry == any(path) })
	if len(kept) == len(list) {
		return nil
	}
	if len(kept) == 0 {
		delete(doc, "instructions")
	} else {
		doc["instructions"] = kept
	}
	return jsonutil.WriteJSON(config, doc)
}

// openCodePlugin is Imprint's OpenCode plugin. When a session goes idle it
// hands {"session_id", "cwd"} to the hook entry point, which starts the
// detached learner (ADR 0077) that exports and learns the session once it
// has settled. OpenCode 2 reports idleness as session.status; earlier
// versions as session.idle, which 2 still sends. One session is handed over
// at most once every few seconds; the learner coalesces the rest.
func openCodePlugin(bp backendPaths) string {
	quote := func(value string) string {
		data, _ := json.Marshal(value)
		return string(data)
	}
	hook := filepath.Join(bp.ProjectDir, "imprint", "hook_entry.py")
	return `// Imprint: learn each OpenCode session when it goes idle.
// Generated by imprint setup; imprint disconnect removes it.
import { spawn } from "node:child_process"

const PYTHON = ` + quote(bp.VenvPython) + `
const HOOK = ` + quote(hook) + `
const DATA = ` + quote(bp.DataDir) + `
const handed = new Map()

function learn(sessionID, cwd) {
  const now = Date.now()
  if (now - (handed.get(sessionID) ?? 0) < 5000) return
  handed.set(sessionID, now)
  try {
    const child = spawn(PYTHON, [HOOK, "opencode-idle", DATA], {
      detached: true,
      stdio: ["pipe", "ignore", "ignore"],
      windowsHide: true,
    })
    child.on("error", () => {})
    child.stdin.on("error", () => {})
    child.stdin.end(JSON.stringify({ session_id: sessionID, cwd }))
    child.unref()
  } catch {}
}

export const ImprintPlugin = async ({ directory }) => ({
  event: async ({ event }) => {
    const props = event?.properties ?? event?.data ?? {}
    const idle = event?.type === "session.idle" || (event?.type === "session.status" && props.status?.type === "idle")
    if (idle && typeof props.sessionID === "string") learn(props.sessionID, directory)
  },
})
`
}
