package cmd

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Wipe destroys imprint data and restarts fresh.
//
//	imprint wipe                    — wipe active workspace
//	imprint wipe --workspace <name> — wipe a specific workspace
//	imprint wipe --all              — wipe everything (all workspaces)
//	imprint wipe --force            — skip confirmation
func Wipe(args []string) {
	force := false
	wipeAll := false
	targetWorkspace := ""

	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--force", "-f":
			force = true
		case "--all":
			wipeAll = true
		case "--workspace", "-w":
			if i+1 < len(args) {
				i++
				targetWorkspace = args[i]
			} else {
				output.Fail("--workspace requires a name")
			}
		}
	}

	projectDir := platform.FindProjectDir()
	dataDir := platform.DataDir(projectDir)

	if !platform.DirExists(dataDir) {
		output.Info("no data directory found — nothing to wipe")
		return
	}

	if wipeAll {
		wipeEverything(projectDir, dataDir, force)
	} else {
		ws := targetWorkspace
		if ws == "" {
			// Read active workspace from config
			cfg := readWorkspaceConfig(dataDir)
			ws = cfg.Active
		}
		wipeSingleWorkspace(projectDir, dataDir, ws, force)
	}
}

// wipeSingleWorkspace deletes a single workspace's collection + DB + WAL.
// Does NOT stop/restart Qdrant — other workspaces may be in use.
func wipeSingleWorkspace(projectDir, dataDir, workspace string, force bool) {
	coll := collectionName(workspace)

	fmt.Println()
	output.Warn(fmt.Sprintf("this will permanently delete workspace '%s' data:", workspace))
	fmt.Printf("    collection: %s\n", coll)
	if workspace == "default" {
		fmt.Printf("    %s/imprint_graph.sqlite3\n", dataDir)
		fmt.Printf("    %s/wal.jsonl\n", dataDir)
	} else {
		fmt.Printf("    %s/imprint_graph_%s.sqlite3\n", dataDir, workspace)
		fmt.Printf("    %s/wal_%s.jsonl\n", dataDir, workspace)
	}
	fmt.Println()

	if !force {
		fmt.Print("type 'wipe' to confirm: ")
		reader := bufio.NewReader(os.Stdin)
		answer, _ := reader.ReadString('\n')
		answer = strings.TrimSpace(answer)
		if answer != "wipe" {
			output.Info("aborted")
			return
		}
	}

	// Delete Qdrant collection via API (no server restart needed)
	venvPython := platform.VenvPython(projectDir)
	if platform.FileExists(venvPython) {
		envVars := []string{
			"PYTHONPATH=" + projectDir,
			"IMPRINT_DATA_DIR=" + dataDir,
		}
		output.Info("deleting collection: " + coll)
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", fmt.Sprintf(`
from imprint import qdrant_runner
from qdrant_client import QdrantClient
h, p = qdrant_runner.ensure_running()
c = QdrantClient(host=h, port=p, timeout=10)
try:
    c.delete_collection(%q)
    print("deleted")
except Exception as e:
    print(f"skip: {e}")
`, coll))
		if err != nil {
			output.Warn("collection delete failed: " + err.Error())
		} else {
			out = strings.TrimSpace(out)
			if out == "deleted" {
				output.Success("removed collection " + coll)
			} else {
				output.Info(out)
			}
		}
	}

	// Delete SQLite DB files
	var dbBase string
	if workspace == "default" {
		dbBase = "imprint_graph.sqlite3"
	} else {
		dbBase = fmt.Sprintf("imprint_graph_%s.sqlite3", workspace)
	}
	for _, suffix := range []string{"", "-shm", "-wal"} {
		path := filepath.Join(dataDir, dbBase+suffix)
		if platform.FileExists(path) {
			if err := os.Remove(path); err != nil {
				output.Warn("failed to remove " + filepath.Base(path) + ": " + err.Error())
			} else {
				output.Success("removed " + filepath.Base(path))
			}
		}
	}

	// Delete WAL file
	var walName string
	if workspace == "default" {
		walName = "wal.jsonl"
	} else {
		walName = fmt.Sprintf("wal_%s.jsonl", workspace)
	}
	walPath := filepath.Join(dataDir, walName)
	if platform.FileExists(walPath) {
		if err := os.Remove(walPath); err != nil {
			output.Warn("failed to remove WAL: " + err.Error())
		} else {
			output.Success("removed " + walName)
		}
	}

	fmt.Println()
	output.Success(fmt.Sprintf("workspace '%s' wiped", workspace))
}

// wipeEverything is the old behavior: stop Qdrant, delete ALL data, restart.
func wipeEverything(projectDir, dataDir string, force bool) {
	targets := []string{
		"qdrant_storage/    (vector store)",
		"qdrant_snapshots/  (snapshots)",
		"imprint_graph*     (knowledge graphs)",
		"wal*.jsonl         (write-ahead logs)",
		".sessions/         (session state)",
		"workspace.json     (workspace config)",
	}

	fmt.Println()
	output.Warn("this will permanently delete ALL imprint data (all workspaces):")
	for _, t := range targets {
		fmt.Printf("    %s/%s\n", dataDir, t)
	}
	fmt.Println()

	if !force {
		fmt.Print("type 'wipe' to confirm: ")
		reader := bufio.NewReader(os.Stdin)
		answer, _ := reader.ReadString('\n')
		answer = strings.TrimSpace(answer)
		if answer != "wipe" {
			output.Info("aborted")
			return
		}
	}

	// Stop Qdrant first
	venvPython := platform.VenvPython(projectDir)
	if platform.FileExists(venvPython) {
		envVars := []string{
			"PYTHONPATH=" + projectDir,
			"IMPRINT_DATA_DIR=" + dataDir,
		}
		output.Info("stopping Qdrant server...")
		runner.RunCaptureEnv(venvPython, envVars,
			"-c", `from imprint import qdrant_runner as q; q.stop()`)
	}

	// Remove directories
	for _, dir := range []struct {
		path  string
		label string
	}{
		{filepath.Join(dataDir, "qdrant_storage"), "vector store"},
		{filepath.Join(dataDir, "qdrant_snapshots"), "snapshots"},
		{filepath.Join(dataDir, ".sessions"), "sessions"},
	} {
		if platform.DirExists(dir.path) {
			if err := os.RemoveAll(dir.path); err != nil {
				output.Warn("failed to remove " + dir.label + ": " + err.Error())
			} else {
				output.Success("removed " + dir.label)
			}
		}
	}

	// Remove all imprint_graph*.sqlite3* files (all workspaces)
	matches, _ := filepath.Glob(filepath.Join(dataDir, "imprint_graph*.sqlite3*"))
	for _, m := range matches {
		if err := os.Remove(m); err != nil {
			output.Warn("failed to remove " + filepath.Base(m) + ": " + err.Error())
		} else {
			output.Success("removed " + filepath.Base(m))
		}
	}

	// Remove all wal*.jsonl files
	walMatches, _ := filepath.Glob(filepath.Join(dataDir, "wal*.jsonl"))
	for _, m := range walMatches {
		if err := os.Remove(m); err != nil {
			output.Warn("failed to remove " + filepath.Base(m) + ": " + err.Error())
		} else {
			output.Success("removed " + filepath.Base(m))
		}
	}

	// Remove other files
	for _, name := range []string{"qdrant.pid", "workspace.json"} {
		path := filepath.Join(dataDir, name)
		if platform.FileExists(path) {
			if err := os.Remove(path); err != nil {
				output.Warn("failed to remove " + name + ": " + err.Error())
			} else {
				output.Success("removed " + name)
			}
		}
	}

	// Restart Qdrant
	if platform.FileExists(venvPython) {
		envVars := []string{
			"PYTHONPATH=" + projectDir,
			"IMPRINT_DATA_DIR=" + dataDir,
		}
		output.Info("restarting Qdrant server...")
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", `from imprint import qdrant_runner as q; h,p=q.ensure_running(); print(f"running at {h}:{p}")`)
		if err != nil {
			output.Warn("server restart failed: " + err.Error() + "\n" + out)
		} else {
			output.Success(out)
		}
	}

	fmt.Println()
	output.Success("wipe complete — all imprint data deleted")
}
