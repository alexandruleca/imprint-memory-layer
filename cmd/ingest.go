package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
	"github.com/hunter/knowledge/internal/runner"
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

func Ingest(args []string) {
	batchSize, args := parseBatchSizeFlag(args)

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'knowledge setup' first")
	}

	envVars := []string{
		"PYTHONPATH=" + projectDir,
		"KNOWLEDGE_DATA_DIR=" + dataDir,
	}

	fmt.Println()
	output.Header("═══ Knowledge Ingest ═══")
	fmt.Println()

	// Step 1: Migrate Claude Code auto-memory files
	output.Info("Step 1/3: Migrating Claude Code memory files...")
	runPython(venvPython, envVars, migrateScript(projectDir, dataDir))
	fmt.Println()

	// Step 2: Index conversations
	output.Info("Step 2/3: Indexing conversation transcripts...")
	cmd := runner.CommandWithEnv(venvPython,
		[]string{"-m", "knowledgebase.cli_conversations", "--all"},
		envVars...,
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	cmd.Run()
	fmt.Println()

	// Step 3: Index project directories if provided
	if len(args) > 0 {
		output.Info("Step 3/3: Indexing project files...")
		targetDir, _ := filepath.Abs(args[0])
		indexDir(venvPython, envVars, targetDir, projectDir, dataDir, batchSize)
	} else {
		output.Info("Step 3/3: Skipped — no directory provided")
		fmt.Println("  Tip: run 'knowledge ingest [--batch-size N] ~/code' to also index project files")
	}

	fmt.Println()
	output.Header("═══ Ingest Complete ═══")

	// Show final stats
	runPython(venvPython, envVars, `
from knowledgebase import vectorstore as vs
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
	cmd.Run()
}

func indexDir(venvPython string, envVars []string, targetDir, projectDir, dataDir string, batchSize int) {
	if !platform.DirExists(targetDir) {
		output.Warn("Directory not found: " + targetDir)
		return
	}

	// Use Python project detector to find real project roots with canonical names
	detectScript := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from knowledgebase.projects import find_projects
projects = find_projects(%q)
print(json.dumps([{"name": p["name"], "path": p["path"], "type": p["type"]} for p in projects]))
`, projectDir, targetDir)

	out, err := runner.RunCapture(venvPython, "-c", detectScript)
	if err != nil {
		output.Warn("Project detection failed: " + err.Error())
		return
	}

	var projects []struct {
		Name string `json:"name"`
		Path string `json:"path"`
		Type string `json:"type"`
	}
	if err := json.Unmarshal([]byte(out), &projects); err != nil {
		output.Warn("Cannot parse projects: " + err.Error())
		return
	}

	if len(projects) == 0 {
		output.Warn("No projects found in " + targetDir)
		return
	}

	output.Info(fmt.Sprintf("Found %d projects", len(projects)))

	pyArgs := []string{"-m", "knowledgebase.cli_index"}
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
	cmd.Run()
}

func migrateScript(projectDir, dataDir string) string {
	return `
import os, sys, json
from pathlib import Path
from knowledgebase import vectorstore as vs

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

    # Derive project name
    name = project_dir.name
    project = "general"
    if "brightspaces" in name:
        project = "brightspaces"
    elif "personal" in name:
        project = "personal"
    elif "knowledge" in name:
        project = "knowledge"

    for f in mem_dir.glob("*.md"):
        content = f.read_text(errors="ignore").strip()
        if not content:
            continue
        vs.store(content=content, project=project, type="decision", source=f"memory/{f.name}")
        stored += 1

print(f"  Migrated {stored} memory files")
`
}
