package cmd

import (
	"os"
	"path/filepath"

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
// imprint graph) and its Board of Tasks. Python names them in
// imprint/config.py (graph_db_path, tasks_db_path).
func workspaceDatabases(workspace string) []string {
	if workspace == "default" {
		return []string{"imprint_graph.sqlite3", "tasks.sqlite3"}
	}
	return []string{"imprint_graph_" + workspace + ".sqlite3", "tasks_" + workspace + ".sqlite3"}
}

// removeWorkspaceDatabases deletes a workspace's SQLite stores with their
// -shm and -wal files, saying what it removed and what it could not.
func removeWorkspaceDatabases(dataDir, workspace string) {
	for _, dbBase := range workspaceDatabases(workspace) {
		for _, suffix := range []string{"", "-shm", "-wal"} {
			path := filepath.Join(dataDir, dbBase+suffix)
			if !platform.FileExists(path) {
				continue
			}
			if err := os.Remove(path); err != nil {
				output.Warn("failed to remove " + filepath.Base(path) + ": " + err.Error())
			} else {
				output.Success("removed " + filepath.Base(path))
			}
		}
	}
}
