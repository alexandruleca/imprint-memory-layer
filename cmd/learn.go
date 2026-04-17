package cmd

import (
	"fmt"
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Learn indexes Claude Code conversation transcripts and auto-memory files.
// Separated from ingest so project file indexing doesn't re-process sessions.
func Learn(args []string) {
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

	fmt.Println()
	output.Header("═══ Imprint Learn ═══")
	fmt.Println()

	// Step 1: Migrate Claude Code auto-memory files
	output.Info("Step 1/2: Migrating Claude Code memory files...")
	runPython(venvPython, envVars, migrateScript(projectDir, dataDir))
	fmt.Println()

	// Step 2: Index conversation transcripts
	output.Info("Step 2/2: Indexing conversation transcripts...")
	cmd := runner.CommandWithEnv(venvPython,
		[]string{"-m", "imprint.cli_conversations", "--all"},
		envVars...,
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	cmd.Run()

	fmt.Println()
	output.Header("═══ Learn Complete ═══")

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
