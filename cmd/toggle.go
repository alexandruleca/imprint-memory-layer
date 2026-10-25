package cmd

import (
	"fmt"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Disable disconnects all managed clients while preserving runtime and memories.
func Disable(args []string) {
	if len(args) != 0 {
		output.Fail("Usage: imprint disable")
	}
	project := platform.FindProjectDir()
	if err := disconnectClients(clients, project); err != nil {
		output.Fail("Cleanup incomplete; runtime preserved for retry: " + err.Error())
	}
	uiStop(nil)
	python := platform.VenvPython(project)
	if platform.FileExists(python) {
		out, err := runner.RunCaptureEnv(python, []string{"PYTHONPATH=" + project, "IMPRINT_DATA_DIR=" + platform.DataDir(project)}, "-c", `from imprint import qdrant_runner as q; print('stopped' if q.stop() else 'not running')`)
		if err != nil {
			output.Fail("Could not stop Qdrant: " + err.Error() + ": " + out)
		}
	}
	output.Success("Disabled Imprint. Runtime and memories preserved; run imprint enable to reconnect.")
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

	if !Setup(target) {
		output.Fail("unknown target: " + target + " (expected: " + ClientTargets() + ")")
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
