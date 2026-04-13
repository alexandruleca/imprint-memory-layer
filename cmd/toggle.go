package cmd

import (
	"fmt"
	"os"
	"strings"

	"github.com/hunter/knowledge/internal/jsonutil"
	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
	"github.com/hunter/knowledge/internal/runner"
)

// Disable tears down everything Knowledge wired into the system: stops the
// Qdrant server, removes the MCP registration from Claude Code, and strips
// our hooks from settings.json. The Python venv and data directory are
// left intact so re-enabling is fast and no memories are lost.
func Disable(args []string) {
	fmt.Println()
	output.Header("═══ Disabling Knowledge ═══")
	fmt.Println()

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	// 1. Stop Qdrant if it's running.
	if platform.FileExists(venvPython) {
		out, _ := runner.RunCaptureEnv(venvPython,
			[]string{"PYTHONPATH=" + projectDir, "KNOWLEDGE_DATA_DIR=" + dataDir},
			"-c", `from knowledgebase import qdrant_runner as q; print('stopped' if q.stop() else 'not running')`)
		output.Info("Qdrant: " + out)
	} else {
		output.Skip("Python venv not found — skipping server stop")
	}

	// 2. Unregister the MCP server from Claude Code.
	if _, ok := runner.Exists("claude"); ok {
		mcpOut, _ := runner.RunCapture("claude", "mcp", "list")
		if strings.Contains(mcpOut, "knowledge") {
			if err := runner.Run("claude", "mcp", "remove", "knowledge"); err != nil {
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
	// substring — every hook we install shells out to a knowledgebase.cli_*
	// module or touches the data dir's session sentinel directory.
	settings := platform.ClaudeSettingsPath()
	needles := []string{
		"knowledgebase.cli_conversations",
		"knowledgebase.cli_extract",
		"knowledgebase.cli_index",
		"COMPACTION IMMINENT",
		"Knowledge MCP available",
		"Knowledge MCP gate",
		dataDir + "/.sessions",
	}
	removed, err := jsonutil.RemoveHooksMatching(settings, needles)
	if err != nil {
		output.Warn("settings update failed: " + err.Error())
	} else if removed == 0 {
		output.Skip("No knowledge hooks present in " + settings)
	} else {
		output.Success(fmt.Sprintf("Removed %d hook(s) from %s", removed, settings))
	}

	fmt.Println()
	output.Header("═══ Disabled ═══")
	fmt.Println()
	fmt.Println("  Data + venv preserved. Run `knowledge enable` to wire it back up.")
	fmt.Println()
	_ = args
	_ = os.Stdout
}

// Enable re-runs setup — idempotent install path that re-registers the MCP
// server, re-installs hooks, ensures venv + data dir, and starts Qdrant.
func Enable(args []string) {
	target := "claude-code"
	if len(args) > 0 {
		target = args[0]
	}

	fmt.Println()
	output.Header("═══ Enabling Knowledge ═══")
	fmt.Printf("  Target: %s\n", target)
	fmt.Println()

	switch target {
	case "claude-code", "claude":
		SetupClaudeCode()
	case "cursor":
		SetupCursor()
	default:
		output.Fail("unknown target: " + target + " (expected: claude-code | cursor)")
	}

	// Pre-warm the Qdrant server so the next MCP call doesn't pay the
	// download/startup cost in the user's session.
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)
	if platform.FileExists(venvPython) {
		out, _ := runner.RunCaptureEnv(venvPython,
			[]string{"PYTHONPATH=" + projectDir, "KNOWLEDGE_DATA_DIR=" + dataDir},
			"-c", `from knowledgebase import qdrant_runner as q; h,p=q.ensure_running(); print(f'qdrant ready at {h}:{p}')`)
		output.Info(out)
	}

	fmt.Println()
	output.Header("═══ Enabled ═══")
	fmt.Println()
}
