package cmd

import (
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Migrate moves memories between workspaces by project or topic.
//
//	imprint migrate --from WS1 --to WS2 --project NAME [--dry-run]
//	imprint migrate --from WS1 --to WS2 --topic TOPIC [--dry-run]
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
