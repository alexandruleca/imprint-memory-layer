package cmd

import (
	"os"
	"path/filepath"
	"testing"
)

// Deleting a workspace removes its facts and its Board with their -wal and
// -shm files, and leaves other workspaces' stores alone.
func TestRemoveWorkspaceDatabasesTakesOnlyThatWorkspace(t *testing.T) {
	dir := t.TempDir()
	mine := []string{"imprint_graph_work.sqlite3", "tasks_work.sqlite3", "tasks_work.sqlite3-wal", "tasks_work.sqlite3-shm"}
	theirs := []string{"imprint_graph.sqlite3", "tasks.sqlite3", "tasks_workshop.sqlite3"}
	for _, name := range append(append([]string{}, mine...), theirs...) {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	removeWorkspaceDatabases(dir, "work")
	for _, name := range mine {
		if _, err := os.Stat(filepath.Join(dir, name)); !os.IsNotExist(err) {
			t.Errorf("%s was not removed", name)
		}
	}
	for _, name := range theirs {
		if _, err := os.Stat(filepath.Join(dir, name)); err != nil {
			t.Errorf("%s was removed: %v", name, err)
		}
	}
	if got := workspaceDatabases("default"); got[1] != "tasks.sqlite3" {
		t.Errorf("the default workspace's Board is %q", got[1])
	}
}
