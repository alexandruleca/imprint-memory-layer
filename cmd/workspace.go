package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

var workspaceNameRE = regexp.MustCompile(`^[a-z0-9][a-z0-9-]*$`)

type workspaceConfig struct {
	Active string   `json:"active"`
	Known  []string `json:"known"`
}

func readWorkspaceConfig(dataDir string) workspaceConfig {
	path := filepath.Join(dataDir, "workspace.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return workspaceConfig{Active: "default", Known: []string{"default"}}
	}
	var cfg workspaceConfig
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return workspaceConfig{Active: "default", Known: []string{"default"}}
	}
	if cfg.Active == "" {
		cfg.Active = "default"
	}
	if len(cfg.Known) == 0 {
		cfg.Known = []string{"default"}
	}
	return cfg
}

func writeWorkspaceConfig(dataDir string, cfg workspaceConfig) error {
	path := filepath.Join(dataDir, "workspace.json")
	raw, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(raw, '\n'), 0644)
}

func validateWorkspaceName(name string) error {
	if name == "" {
		return fmt.Errorf("name cannot be empty")
	}
	if len(name) > 40 {
		return fmt.Errorf("name too long (max 40)")
	}
	if !workspaceNameRE.MatchString(name) {
		return fmt.Errorf("must be lowercase alphanumeric + hyphens, start with letter/digit")
	}
	return nil
}

func collectionName(workspace string) string {
	if workspace == "default" {
		return "memories"
	}
	return "memories_" + workspace
}

// Workspace dispatches workspace subcommands.
//
//	imprint workspace              — list
//	imprint workspace list         — list
//	imprint workspace switch <n>   — switch (create if new)
//	imprint workspace <n>          — shortcut for switch
//	imprint workspace delete <n>   — delete workspace data
func Workspace(args []string) {
	projectDir := platform.FindProjectDir()
	dataDir := platform.DataDir(projectDir)

	if len(args) == 0 {
		workspaceList(dataDir)
		return
	}

	switch args[0] {
	case "list":
		workspaceList(dataDir)
	case "switch":
		if len(args) < 2 {
			output.Fail("usage: imprint workspace switch <name>")
		}
		workspaceSwitch(dataDir, args[1])
	case "delete":
		if len(args) < 2 {
			output.Fail("usage: imprint workspace delete <name>")
		}
		workspaceDelete(projectDir, dataDir, args[1])
	default:
		// Bare argument = shortcut for switch
		workspaceSwitch(dataDir, args[0])
	}
}

func workspaceList(dataDir string) {
	cfg := readWorkspaceConfig(dataDir)
	fmt.Println()
	output.Header("Workspaces")
	fmt.Println()
	for _, ws := range cfg.Known {
		if ws == cfg.Active {
			fmt.Printf("  %s (active)\n", ws)
		} else {
			fmt.Printf("  %s\n", ws)
		}
	}
	fmt.Println()
}

func workspaceSwitch(dataDir string, name string) {
	if err := validateWorkspaceName(name); err != nil {
		output.Fail("invalid workspace name: " + err.Error())
	}

	cfg := readWorkspaceConfig(dataDir)
	cfg.Active = name

	// Add to known if new
	found := false
	for _, ws := range cfg.Known {
		if ws == name {
			found = true
			break
		}
	}
	if !found {
		cfg.Known = append(cfg.Known, name)
	}

	if err := writeWorkspaceConfig(dataDir, cfg); err != nil {
		output.Fail("failed to write config: " + err.Error())
	}

	if found {
		output.Success("switched to workspace: " + name)
	} else {
		output.Success("created and switched to workspace: " + name)
	}
}

func workspaceDelete(projectDir, dataDir, name string) {
	if name == "default" {
		output.Fail("cannot delete the default workspace — use 'imprint wipe' instead")
	}

	cfg := readWorkspaceConfig(dataDir)
	if name == cfg.Active {
		output.Fail("cannot delete the active workspace — switch to a different workspace first")
	}

	// Check if workspace is known
	known := false
	for _, ws := range cfg.Known {
		if ws == name {
			known = true
			break
		}
	}
	if !known {
		output.Fail("workspace not found: " + name)
	}

	coll := collectionName(name)

	// Delete Qdrant collection via Python
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
	for _, suffix := range []string{"", "-shm", "-wal"} {
		path := filepath.Join(dataDir, fmt.Sprintf("imprint_graph_%s.sqlite3%s", name, suffix))
		if platform.FileExists(path) {
			if err := os.Remove(path); err != nil {
				output.Warn("failed to remove " + path + ": " + err.Error())
			} else {
				output.Success("removed " + filepath.Base(path))
			}
		}
	}

	// Delete WAL file
	walPath := filepath.Join(dataDir, fmt.Sprintf("wal_%s.jsonl", name))
	if platform.FileExists(walPath) {
		if err := os.Remove(walPath); err != nil {
			output.Warn("failed to remove WAL: " + err.Error())
		} else {
			output.Success("removed " + filepath.Base(walPath))
		}
	}

	// Remove from known list
	var newKnown []string
	for _, ws := range cfg.Known {
		if ws != name {
			newKnown = append(newKnown, ws)
		}
	}
	cfg.Known = newKnown
	if err := writeWorkspaceConfig(dataDir, cfg); err != nil {
		output.Warn("failed to update config: " + err.Error())
	}

	fmt.Println()
	output.Success("workspace '" + name + "' deleted")
}
