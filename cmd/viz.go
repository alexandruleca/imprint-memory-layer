package cmd

import (
	"os"

	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
	"github.com/hunter/knowledge/internal/runner"
)

func Viz(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'knowledge setup' first")
	}

	pyArgs := []string{"-m", "knowledgebase.cli_viz"}
	pyArgs = append(pyArgs, args...)

	cmd := runner.CommandWithEnv(venvPython, pyArgs,
		"PYTHONPATH="+projectDir,
		"KNOWLEDGE_DATA_DIR="+dataDir,
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	cmd.Run()
}
