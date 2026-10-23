package cmd

import (
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Migrate moves memories between workspaces by project, topic or source.
// --copy leaves the source workspace intact, e.g. to curate a workspace for
// sharing (`imprint sync serve --workspace WS2 --send-only`).
//
//	imprint migrate --from WS1 --to WS2 --project NAME [--copy] [--dry-run]
//	imprint migrate --from WS1 --to WS2 --topic TOPIC [--copy] [--dry-run]
//	imprint migrate --from WS1 --to WS2 --source KEY [--copy] [--dry-run]
func Migrate(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	pyArgs := []string{"-m", "imprint.cli_migrate"}
	pyArgs = append(pyArgs, args...)

	cmd := runner.CommandWithEnv(venvPython, pyArgs,
		"PYTHONPATH="+projectDir,
		"IMPRINT_DATA_DIR="+dataDir,
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	cmd.Run()
}
