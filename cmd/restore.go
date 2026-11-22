package cmd

import (
	"errors"
	"os"
	"os/exec"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

const restoreUsage = `Usage: imprint restore <dir> [--to <out>] [--all] [--overwrite] [--apply] [--workspace NAME]
       imprint restore --project NAME --to <out> [--all] [--overwrite] [--apply] [--workspace NAME]

Writes indexed files back from their source snapshots. Previews unless --apply.
  <dir>            files this device indexed under <dir>, back where they were (or under --to)
  --project NAME   every file of a project, from any device, under --to
  --all            also compare files that exist; --overwrite replaces those that differ
Documents restore only their extracted text, as <name>.extracted.txt.
`

// Restore writes indexed files back from their source snapshots (ADR 0197).
// It only reads snapshots and writes outside the data folder, so it takes no
// queue lock: a refresh running meanwhile replaces a snapshot atomically.
func Restore(args []string) {
	if len(args) == 0 || args[0] == "-h" || args[0] == "--help" {
		os.Stderr.WriteString(restoreUsage)
		if len(args) == 0 {
			os.Exit(1)
		}
		return
	}
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	child := runner.CommandWithEnv(venvPython, append([]string{"-m", "imprint.cli_restore"}, restoreArgs(args)...),
		"PYTHONPATH="+projectDir, "IMPRINT_DATA_DIR="+dataDir)
	child.Stdout, child.Stderr, child.Stdin = os.Stdout, os.Stderr, os.Stdin
	if err := child.Run(); err != nil {
		// The summary already says what failed.
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			os.Exit(exit.ExitCode())
		}
		output.Fail("Restore failed: " + err.Error())
	}
}

// restoreArgs spells the folder and --to as absolute paths (a Windows path
// typed in WSL as its /mnt form), leaving the other arguments as given.
func restoreArgs(args []string) []string {
	return queueArgs(args, true, []string{"--to"}, []string{"--project", "--workspace"})
}
