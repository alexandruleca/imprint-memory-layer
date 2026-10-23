package cmd

import (
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

func pinWorkspace(dataDir string) {
	if os.Getenv("IMPRINT_WORKSPACE") == "" {
		if err := os.Setenv("IMPRINT_WORKSPACE", readWorkspaceConfig(dataDir).Active); err != nil {
			output.Fail(err.Error())
		}
	}
}

func runWorkspacePolicy(args []string, lock bool) {
	projectDir := platform.FindProjectDir()
	dataDir := platform.DataDir(projectDir)
	pinWorkspace(dataDir)
	python := platform.VenvPython(projectDir)
	if !platform.FileExists(python) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}
	if lock {
		handle := acquireOrFail(dataDir, "purge")
		defer handle.Release()
	}
	child := runner.CommandWithEnv(python, append([]string{"-m", "imprint.cli_workspace"}, args...),
		"PYTHONPATH="+projectDir, "IMPRINT_DATA_DIR="+dataDir)
	child.Stdout, child.Stderr = os.Stdout, os.Stderr
	if err := child.Run(); err != nil {
		output.Fail("Workspace operation failed: " + err.Error())
	}
}

// Purge previews by default; --apply removes indexed data and excludes re-ingestion.
func Purge(args []string) {
	runWorkspacePolicy(append([]string{"purge"}, args...), true)
}
