package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
	"github.com/hunter/knowledge/internal/runner"
)

func Index(args []string) {
	if len(args) == 0 {
		fmt.Fprintf(os.Stderr, `Usage: knowledge index <directory>

Runs mempalace init + mine on every subdirectory.

Examples:
  knowledge index ~/code/brightspaces/node
  knowledge index ~/code
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
	mempalace := platform.VenvBin(projectDir, "mempalace")
	if !platform.FileExists(mempalace) {
		output.Fail("mempalace not found at " + mempalace + " — run 'knowledge setup' first")
	}
	dataDir := platform.DataDir(projectDir)

	// Collect subdirectories (depth 1, skip hidden)
	entries, err := os.ReadDir(targetDir)
	if err != nil {
		output.Fail("Cannot read directory: " + err.Error())
	}

	var dirs []string
	for _, entry := range entries {
		if entry.IsDir() && !strings.HasPrefix(entry.Name(), ".") {
			dirs = append(dirs, filepath.Join(targetDir, entry.Name()))
		}
	}

	if len(dirs) == 0 {
		output.Fail("No subdirectories found in " + targetDir)
	}

	fmt.Println()
	output.Info(fmt.Sprintf("Found %d directories in %s:", len(dirs), targetDir))
	for _, dir := range dirs {
		fmt.Println("    " + filepath.Base(dir))
	}
	fmt.Println()

	indexed := 0
	failed := 0

	for i, dir := range dirs {
		name := filepath.Base(dir)
		output.Info(fmt.Sprintf("[%d/%d] Processing %s...", i+1, len(dirs), name))

		// Init (skip if mempalace.yaml exists)
		yamlPath := filepath.Join(dir, "mempalace.yaml")
		if platform.FileExists(yamlPath) {
			fmt.Println("  [-] init (already done)")
		} else {
			if err := runner.RunIndented("  ", mempalace, "--palace", dataDir, "init", dir, "--yes"); err != nil {
				fmt.Println("  [x] init failed")
				failed++
				fmt.Println()
				continue
			}
			fmt.Println("  [+] init complete")
		}

		// Mine
		if err := runner.RunIndented("  ", mempalace, "--palace", dataDir, "mine", dir); err != nil {
			fmt.Println("  [x] mine failed")
			failed++
		} else {
			fmt.Println("  [+] mine complete")
			indexed++
		}

		fmt.Println()
	}

	// Summary
	output.Header("═══ Indexing Complete ═══")
	fmt.Printf("  Indexed:  %d\n", indexed)
	if failed > 0 {
		fmt.Printf("  Failed:   %d\n", failed)
	}
	fmt.Println()
	output.Info("Run 'mempalace status' to see what's been filed.")
	output.Info("Run 'mempalace search \"your query\"' to search across all indexed projects.")
}
