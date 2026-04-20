package cmd

import (
	"fmt"
	"os"
	"strings"

	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
	"github.com/hunter/imprint/internal/tomlutil"
)

// Disable tears down everything Imprint wired into the system: stops the
// Qdrant server, removes the MCP registration from Claude Code, and strips
// our hooks from settings.json. The Python venv and data directory are
// left intact so re-enabling is fast and no memories are lost.
func Disable(args []string) {
	fmt.Println()
	output.Header("═══ Disabling Imprint ═══")
	fmt.Println()

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	// 1. Stop Qdrant if it's running.
	if platform.FileExists(venvPython) {
		out, _ := runner.RunCaptureEnv(venvPython,
			[]string{"PYTHONPATH=" + projectDir, "IMPRINT_DATA_DIR=" + dataDir},
			"-c", `from imprint import qdrant_runner as q; print('stopped' if q.stop() else 'not running')`)
		output.Info("Qdrant: " + out)
	} else {
		output.Skip("Python venv not found — skipping server stop")
	}

	// 2. Unregister the MCP server from Claude Code.
	if _, ok := runner.Exists("claude"); ok {
		mcpOut, _ := runner.RunCapture("claude", "mcp", "list")
		if strings.Contains(mcpOut, "imprint") {
			if err := runner.Run("claude", "mcp", "remove", "imprint"); err != nil {
				output.Warn("MCP remove failed: " + err.Error())
			} else {
				output.Success("Removed MCP server registration")
			}
		} else {
			output.Skip("MCP server not registered")
		}
	} else {
		output.Skip("Claude Code CLI not found — skipping MCP unregister")
	}

	// 3. Strip our hooks from Claude Code settings.json. Match by command
	// substring — every hook we install shells out to a imprint.cli_*
	// module or touches the data dir's session sentinel directory.
	settings := platform.ClaudeSettingsPath()
	needles := []string{
		"imprint.cli_conversations",
		"imprint.cli_extract",
		"imprint.cli_index",
		"COMPACTION IMMINENT",
		"Imprint MCP available",
		"Imprint MCP gate",
		dataDir + "/.sessions",
	}
	removed, err := jsonutil.RemoveHooksMatching(settings, needles)
	if err != nil {
		output.Warn("settings update failed: " + err.Error())
	} else if removed == 0 {
		output.Skip("No imprint hooks present in " + settings)
	} else {
		output.Success(fmt.Sprintf("Removed %d hook(s) from %s", removed, settings))
	}

	// 4. Tear down MCP registration from the other supported hosts. Each
	//    step is best-effort: skip silently if the config file is absent.
	removeJSONServer := func(path, rootKey, label string) {
		if path == "" || !platform.FileExists(path) {
			output.Skip(label + " not present (" + path + ")")
			return
		}
		ok, err := jsonutil.RemoveMCPServer(path, rootKey, "imprint")
		if err != nil {
			output.Warn(label + " update failed: " + err.Error())
		} else if ok {
			output.Success("Removed imprint from " + path)
		} else {
			output.Skip("imprint not registered in " + path)
		}
	}

	// Claude Desktop (mcpServers). WSL-aware — walks the Windows-side
	// config (standalone or MS Store redirect) when running inside WSL.
	if claudeDesktopPath := platform.ClaudeDesktopConfigPath(); claudeDesktopPath != "" {
		removeJSONServer(claudeDesktopPath, "mcpServers", "Claude Desktop MCP config")
	}

	// Cursor (mcpServers).
	removeJSONServer(platform.CursorMCPPath(), "mcpServers", "Cursor MCP config")

	// Copilot user-global mcp.json (servers).
	removeJSONServer(platform.CopilotMCPPath(), "servers", "Copilot MCP config")

	// Cline extension (mcpServers).
	removeJSONServer(platform.ClineExtSettingsPath(), "mcpServers", "Cline extension MCP config")

	// Cline CLI (mcpServers).
	removeJSONServer(platform.ClineCLISettingsPath(), "mcpServers", "Cline CLI MCP config")

	// OpenClaw (mcp.servers — nested two levels).
	removeNestedJSONServer := func(path string, keyPath []string, label string) {
		if path == "" || !platform.FileExists(path) {
			output.Skip(label + " not present (" + path + ")")
			return
		}
		ok, err := jsonutil.RemoveMCPServerNested(path, keyPath, "imprint")
		if err != nil {
			output.Warn(label + " update failed: " + err.Error())
		} else if ok {
			output.Success("Removed imprint from " + path)
		} else {
			output.Skip("imprint not registered in " + path)
		}
	}
	removeNestedJSONServer(platform.OpenClawMCPPath(), []string{"mcp", "servers"}, "OpenClaw MCP config")

	// Codex TOML.
	codexPath := platform.CodexConfigPath()
	if platform.FileExists(codexPath) {
		if ok, err := tomlutil.RemoveMCPServer(codexPath, "imprint"); err != nil {
			output.Warn("Codex config update failed: " + err.Error())
		} else if ok {
			output.Success("Removed imprint from " + codexPath)
		} else {
			output.Skip("imprint not registered in " + codexPath)
		}
	} else {
		output.Skip("Codex config not present (" + codexPath + ")")
	}

	fmt.Println()
	output.Header("═══ Disabled ═══")
	fmt.Println()
	fmt.Println("  Data + venv preserved. Run `imprint enable` to wire it back up.")
	fmt.Println()
	_ = args
	_ = os.Stdout
}

// Enable re-runs setup — idempotent install path that re-registers the MCP
// server, re-installs hooks, ensures venv + data dir, and starts Qdrant.
func Enable(args []string) {
	target := "all"
	if len(args) > 0 {
		target = args[0]
	}

	fmt.Println()
	output.Header("═══ Enabling Imprint ═══")
	fmt.Printf("  Target: %s\n", target)
	fmt.Println()

	if !DispatchSetup(target) {
		output.Fail("unknown target: " + target + " (expected: claude-code | claude-desktop | chatgpt-desktop | cursor | codex | copilot | cline | openclaw | all)")
	}

	// Pre-warm the Qdrant server so the next MCP call doesn't pay the
	// download/startup cost in the user's session.
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)
	if platform.FileExists(venvPython) {
		out, _ := runner.RunCaptureEnv(venvPython,
			[]string{"PYTHONPATH=" + projectDir, "IMPRINT_DATA_DIR=" + dataDir},
			"-c", `from imprint import qdrant_runner as q; h,p=q.ensure_running(); print(f'qdrant ready at {h}:{p}')`)
		output.Info(out)
	}

	fmt.Println()
	output.Header("═══ Enabled ═══")
	fmt.Println()
}
