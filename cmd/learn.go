package cmd

import (
	"fmt"
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Learn indexes Claude Code conversation transcripts and auto-memory files,
// and (with --desktop) also scans the user's Downloads folder(s) for
// Claude Desktop / ChatGPT Desktop export zips and ingests them. Separated
// from ingest so project file indexing doesn't re-process sessions.
//
//	imprint learn              # Claude Code transcripts + auto-memory files
//	imprint learn --desktop    # also index new desktop-app export zips
//	imprint learn --watch      # poll-loop the desktop scanner (implies --desktop)
//	imprint learn --path DIR   # add an extra scan root (repeatable)
func Learn(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	lock := acquireOrEnqueue(dataDir, "learn", args)
	defer lock.Release()

	envVars := []string{
		"PYTHONPATH=" + projectDir,
		"IMPRINT_DATA_DIR=" + dataDir,
	}

	desktop := false
	watch := false
	var passThrough []string
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--desktop":
			desktop = true
		case "--watch":
			desktop = true
			watch = true
			passThrough = append(passThrough, "--watch")
		case "--interval":
			if i+1 < len(args) {
				passThrough = append(passThrough, "--interval", args[i+1])
				i++
			}
		case "--path":
			if i+1 < len(args) {
				passThrough = append(passThrough, "--path", args[i+1])
				i++
			}
		}
	}

	fmt.Println()
	output.Header("═══ Imprint Learn ═══")
	fmt.Println()

	// Always run the Claude Code pipeline (memory migration + transcript
	// indexing). --desktop is additive: on top of Claude Code, also scan
	// Downloads for Claude Desktop / ChatGPT Desktop export zips.
	steps := 2
	if desktop {
		steps = 3
	}

	output.Info(fmt.Sprintf("Step 1/%d: Migrating Claude Code memory files...", steps))
	runPython(venvPython, envVars, migrateScript(projectDir, dataDir))
	fmt.Println()

	output.Info(fmt.Sprintf("Step 2/%d: Indexing Claude Code conversation transcripts...", steps))
	ccCmd := runner.CommandWithEnv(venvPython,
		[]string{"-m", "imprint.cli_conversations", "--all"},
		envVars...,
	)
	ccCmd.Stdout = os.Stdout
	ccCmd.Stderr = os.Stderr
	ccCmd.Stdin = os.Stdin
	ccCmd.Run()
	fmt.Println()

	if desktop {
		label := "Step 3/3: Scanning Downloads for Claude Desktop / ChatGPT Desktop exports"
		if watch {
			label = "Step 3/3: Watching Downloads for Claude Desktop / ChatGPT Desktop exports"
		}
		output.Info(label)
		cmdArgs := append([]string{"-m", "imprint.cli_desktop_learn"}, passThrough...)
		cmd := runner.CommandWithEnv(venvPython, cmdArgs, envVars...)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		cmd.Stdin = os.Stdin
		cmd.Run()
		fmt.Println()
	}

	fmt.Println()
	output.Header("═══ Learn Complete ═══")

	runPython(venvPython, envVars, `
from imprint import vectorstore as vs
s = vs.status()
print(f"  Total memories: {s['total_memories']}")
for p, c in sorted(s['by_project'].items(), key=lambda x: -x[1]):
    print(f"    {p}: {c}")
`)
	fmt.Println()
}
