package cmd

import (
	"errors"
	"os"
	"os/exec"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

const upkeepUsage = `Usage: imprint upkeep [--dry-run]

Brings what is already stored up to date now, as the dashboard does while its
queue is idle (ADR 0201): memories synced from paired devices are dated to
when they were first stored, and large data files this device indexed are
kept whole. --dry-run says what it would change without changing it.
`

// Upkeep runs the steps background upkeep has left, in the foreground. It
// writes memories, so it holds the queue lock, or joins the dashboard's
// queue when the dashboard holds it.
func Upkeep(args []string) {
	for _, arg := range args {
		if arg == "-h" || arg == "--help" {
			os.Stdout.WriteString(upkeepUsage)
			return
		}
		if arg != "--dry-run" {
			os.Stderr.WriteString(upkeepUsage)
			os.Exit(2)
		}
	}
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	lock := acquireOrEnqueue(dataDir, "upkeep", args)
	defer lock.Release()

	child := runner.CommandWithEnv(venvPython, append([]string{"-m", "imprint.upkeep"}, args...),
		"PYTHONPATH="+projectDir, "IMPRINT_DATA_DIR="+dataDir)
	child.Stdout, child.Stderr, child.Stdin = os.Stdout, os.Stderr, os.Stdin
	if err := child.Run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			lock.Release()
			os.Exit(exit.ExitCode())
		}
		output.Fail("Upkeep failed: " + err.Error())
	}
}
