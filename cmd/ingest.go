package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// parseBatchSizeFlag strips --batch-size N / --batch-size=N from args.
// Returns (batchSize, remaining). batchSize = 0 means "not provided, use Python default".
func parseBatchSizeFlag(args []string) (int, []string) {
	out := make([]string, 0, len(args))
	batchSize := 0
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--batch-size" {
			if i+1 >= len(args) {
				output.Fail("--batch-size requires a value")
			}
			n, err := strconv.Atoi(args[i+1])
			if err != nil || n < 1 {
				output.Fail(fmt.Sprintf("--batch-size must be a positive integer, got %q", args[i+1]))
			}
			batchSize = n
			i++
			continue
		}
		if strings.HasPrefix(a, "--batch-size=") {
			v := strings.TrimPrefix(a, "--batch-size=")
			n, err := strconv.Atoi(v)
			if err != nil || n < 1 {
				output.Fail(fmt.Sprintf("--batch-size must be a positive integer, got %q", v))
			}
			batchSize = n
			continue
		}
		out = append(out, a)
	}
	return batchSize, out
}

func parseForceFlag(args []string) (bool, []string) {
	force := false
	out := make([]string, 0, len(args))
	for _, arg := range args {
		if arg == "--force" {
			force = true
		} else {
			out = append(out, arg)
		}
	}
	return force, out
}

func Ingest(args []string) {
	originalArgs := append([]string(nil), args...)
	batchSize, args := parseBatchSizeFlag(args)
	force, args := parseForceFlag(args)

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)
	pinWorkspace(dataDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	if len(args) == 0 {
		output.Fail("Usage: imprint ingest <path> [--batch-size N] [--force]\n  <path> can be a directory or a single file\n  Tip: run 'imprint learn' to index conversations + memory files")
	}

	lock := acquireOrEnqueue(dataDir, "ingest", originalArgs)
	defer lock.Release()

	envVars := []string{
		"PYTHONPATH=" + projectDir,
		"IMPRINT_DATA_DIR=" + dataDir,
	}

	fmt.Println()
	output.Header("═══ Imprint Ingest ═══")
	fmt.Println()

	target, _ := filepath.Abs(platform.TranslateWSLPath(args[0]))

	// Detect file vs directory
	info, err := os.Stat(target)
	if err != nil {
		output.Fail("Path not found: " + target)
	}

	if info.IsDir() {
		output.Info("Indexing project files...")
		indexDir(venvPython, envVars, target, projectDir, dataDir, batchSize, force)
	} else {
		output.Info("Indexing single file...")
		indexFile(venvPython, envVars, target, projectDir, batchSize, force)
	}

	fmt.Println()
	output.Header("═══ Ingest Complete ═══")

	// Show final stats
	runPython(venvPython, envVars, `
from imprint import vectorstore as vs
s = vs.status()
print(f"  Total memories: {s['total_memories']}")
for p, c in sorted(s['by_project'].items(), key=lambda x: -x[1]):
    print(f"    {p}: {c}")
`)
	fmt.Println()
}

func runPython(venvPython string, envVars []string, script string) {
	cmd := runner.CommandWithEnv(venvPython, []string{"-c", script}, envVars...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		output.Fail("Indexing command failed: " + err.Error())
	}
}

func indexDir(venvPython string, envVars []string, targetDir, projectDir, dataDir string, batchSize int, force bool) {
	if !platform.DirExists(targetDir) {
		output.Warn("Directory not found: " + targetDir)
		return
	}

	// Use Python project detector to find real project roots with canonical names
	detectScript := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from imprint.projects import find_projects
projects = find_projects(%q)
print(json.dumps([{"name": p["name"], "path": p["path"], "type": p["type"]} for p in projects]))
`, projectDir, targetDir)

	out, err := runner.RunCapture(venvPython, "-c", detectScript)
	if err != nil {
		output.Fail("Project detection failed: " + err.Error())
		return
	}

	var projects []struct {
		Name string `json:"name"`
		Path string `json:"path"`
		Type string `json:"type"`
	}
	if err := json.Unmarshal([]byte(out), &projects); err != nil {
		output.Fail("Cannot parse projects: " + err.Error())
		return
	}

	if len(projects) == 0 {
		output.Warn("No projects found in " + targetDir)
		return
	}

	output.Info(fmt.Sprintf("Found %d projects", len(projects)))

	pyArgs := []string{"-m", "imprint.cli_index"}
	if force {
		pyArgs = append(pyArgs, "--force")
	}
	if batchSize > 0 {
		pyArgs = append(pyArgs, "--batch-size", strconv.Itoa(batchSize))
	}
	pyArgs = append(pyArgs, targetDir)
	for _, p := range projects {
		pyArgs = append(pyArgs, p.Path+":"+p.Name)
	}

	cmd := runner.CommandWithEnv(venvPython, pyArgs, envVars...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		output.Fail("Indexing command failed: " + err.Error())
	}
}

func indexFile(venvPython string, envVars []string, filePath, projectDir string, batchSize int, force bool) {
	// Detect project from the file's parent directory
	parentDir := filepath.Dir(filePath)
	detectScript := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from imprint.projects import find_projects
projects = find_projects(%q, max_depth=0)
print(json.dumps([{"name": p["name"], "path": p["path"]} for p in projects]))
`, projectDir, parentDir)

	projectName := filepath.Base(parentDir) // fallback
	out, err := runner.RunCapture(venvPython, "-c", detectScript)
	if err == nil {
		var projects []struct {
			Name string `json:"name"`
			Path string `json:"path"`
		}
		if json.Unmarshal([]byte(out), &projects) == nil && len(projects) > 0 {
			projectName = projects[0].Name
		}
	}

	pyArgs := []string{"-m", "imprint.cli_index", "--file", filePath, "--project", projectName}
	if force {
		pyArgs = append(pyArgs, "--force")
	}
	if batchSize > 0 {
		pyArgs = append(pyArgs, "--batch-size", strconv.Itoa(batchSize))
	}

	cmd := runner.CommandWithEnv(venvPython, pyArgs, envVars...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		output.Fail("Indexing command failed: " + err.Error())
	}
}

func migrateScript(projectDir, dataDir string) string {
	return `
import os, sys, json
from pathlib import Path
from imprint import vectorstore as vs
from imprint.workspace_policy import transcript_allowed

claude_dir = Path.home() / ".claude" / "projects"
if not claude_dir.exists():
    print("  No Claude Code projects found.")
    sys.exit(0)

stored = 0
for project_dir in claude_dir.iterdir():
    if not project_dir.is_dir():
        continue
    mem_dir = project_dir / "memory"
    if not mem_dir.exists():
        continue
    # Claude's folder encoding is ambiguous. Require real transcript cwd
    # evidence for this project before importing its auto-memory files.
    transcripts = list(project_dir.glob("*.jsonl"))
    if not transcripts or not all(transcript_allowed(path) for path in transcripts):
        continue

    # Derive project name
    name = project_dir.name
    project = "general"
    if "personal" in name:
        project = "personal"
    elif "imprint" in name:
        project = "imprint"

    for f in mem_dir.glob("*.md"):
        content = f.read_text(errors="ignore").strip()
        if not content:
            continue
        from imprint.workspace_policy import is_excluded
        if is_excluded(path=str(f), source=f"memory/{f.name}"):
            continue
        vs.store(content=content, project=project, type="decision", source=f"memory/{f.name}", source_path=str(f.resolve()), source_type="memory")
        stored += 1

print(f"  Migrated {stored} memory files")
`
}
