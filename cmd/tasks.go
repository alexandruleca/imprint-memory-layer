package cmd

import (
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Tasks shows and changes the workspace's Board of Tasks (ADRs 0208-0216),
// implemented by imprint.cli_tasks: `imprint tasks --help` lists the commands.
func Tasks(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}
	child := runner.CommandWithEnv(venvPython, append([]string{"-m", "imprint.cli_tasks"}, args...),
		"PYTHONPATH="+projectDir, "IMPRINT_DATA_DIR="+platform.DataDir(projectDir))
	child.Stdout, child.Stderr, child.Stdin = os.Stdout, os.Stderr, os.Stdin
	if err := child.Run(); err != nil {
		if exit, ok := err.(interface{ ExitCode() int }); ok && exit.ExitCode() > 0 {
			os.Exit(exit.ExitCode())
		}
		output.Fail("imprint tasks failed: " + err.Error())
	}
}

// workspaceDatabases are a workspace's SQLite stores: its facts (the
// imprint graph) and its Board of Tasks.
func workspaceDatabases(workspace string) []string {
	if workspace == "default" {
		return []string{"imprint_graph.sqlite3", "tasks.sqlite3"}
	}
	return []string{"imprint_graph_" + workspace + ".sqlite3", "tasks_" + workspace + ".sqlite3"}
}
