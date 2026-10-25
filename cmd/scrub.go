package cmd

import (
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Scrub counts the secrets stored in a workspace's memories and facts;
// --apply redacts them and embeds the changed memories again (ADR 0060).
// It rewrites memories, so it takes the queue lock like retag.
func Scrub(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	lock := acquireOrEnqueue(dataDir, "scrub", args)
	defer lock.Release()

	child := runner.CommandWithEnv(venvPython, append([]string{"-m", "imprint.cli_scrub"}, args...),
		"PYTHONPATH="+projectDir, "IMPRINT_DATA_DIR="+dataDir)
	child.Stdout, child.Stderr, child.Stdin = os.Stdout, os.Stderr, os.Stdin
	if err := child.Run(); err != nil {
		output.Fail("Scrub failed: " + err.Error())
	}
}
