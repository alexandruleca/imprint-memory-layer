package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

func Refresh(args []string) {
	if len(args) == 0 {
		fmt.Fprintf(os.Stderr, `Usage: imprint refresh <directory>

Re-indexes only files that changed since last indexing.

Examples:
  imprint refresh ~/code/brightspaces
  imprint refresh ~/code
`)
		os.Exit(1)
	}

	targetDir, err := filepath.Abs(args[0])
	if err != nil {
		output.Fail("Invalid path: " + args[0])
	}

	if !platform.DirExists(targetDir) {
		output.Fail("Directory not found: " + args[0])
	}

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	envVars := []string{
		"PYTHONPATH=" + projectDir,
		"IMPRINT_DATA_DIR=" + dataDir,
	}

	// Detect projects
	detectScript := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from imprint.projects import find_projects
projects = find_projects(%q)
print(json.dumps([{"name": p["name"], "path": p["path"]} for p in projects]))
`, projectDir, targetDir)

	out, err := runner.RunCapture(venvPython, "-c", detectScript)
	if err != nil {
		output.Fail("Project detection failed: " + err.Error())
	}

	var projects []struct {
		Name string `json:"name"`
		Path string `json:"path"`
	}
	if err := json.Unmarshal([]byte(out), &projects); err != nil {
		output.Fail("Cannot parse projects: " + err.Error())
	}

	if len(projects) == 0 {
		output.Fail("No projects found in " + targetDir)
	}

	pyArgs := []string{"-m", "imprint.cli_refresh", targetDir}
	for _, p := range projects {
		pyArgs = append(pyArgs, p.Path+":"+p.Name)
	}

	cmd := runner.CommandWithEnv(venvPython, pyArgs, envVars...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	cmd.Run()
}
