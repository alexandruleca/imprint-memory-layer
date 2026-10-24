package cmd

import (
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Move previews moving a project's memories, or some of its sources, to
// another project; --apply moves them and keeps later writes there (ADR 0036).
// It holds the ingestion lock, as purge does.
func Move(args []string) {
	projectDir := platform.FindProjectDir()
	dataDir := platform.DataDir(projectDir)
	pinWorkspace(dataDir)
	python := platform.VenvPython(projectDir)
	if !platform.FileExists(python) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}
	handle := acquireOrFail(dataDir, "move")
	defer handle.Release()
	child := runner.CommandWithEnv(python, append([]string{"-m", "imprint.projects"}, args...),
		"PYTHONPATH="+projectDir, "IMPRINT_DATA_DIR="+dataDir)
	child.Stdout, child.Stderr = os.Stdout, os.Stderr
	if err := child.Run(); err != nil {
		output.Fail("Move failed: " + err.Error())
	}
}
