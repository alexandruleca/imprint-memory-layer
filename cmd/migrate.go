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

// projectMapping maps Claude Code project directory names to wing names for mining.
var projectMapping = map[string]string{
	"brightspaces-node":   "brightspaces",
	"brightspaces-python": "brightspaces",
	"personal":            "personal",
}

func Migrate() {
	projectDir := platform.FindProjectDir()
	mempalace := platform.VenvBin(projectDir, "mempalace")

	if !platform.FileExists(mempalace) {
		output.Fail("mempalace not found — run 'knowledge setup' first")
	}

	dataDir := platform.DataDir(projectDir)

	// Find all memory directories under ~/.claude/projects/
	claudeProjectsDir := filepath.Join(platform.HomeDir(), ".claude", "projects")
	if !platform.DirExists(claudeProjectsDir) {
		output.Fail("No Claude Code projects directory found at " + claudeProjectsDir)
	}

	entries, err := os.ReadDir(claudeProjectsDir)
	if err != nil {
		output.Fail("Cannot read " + claudeProjectsDir + ": " + err.Error())
	}

	// Collect projects that have memory files
	type memoryProject struct {
		name      string // human-readable project name
		wing      string // wing to mine under
		memoryDir string // path to memory/ directory
		files     []string
	}

	var projects []memoryProject

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		memDir := filepath.Join(claudeProjectsDir, entry.Name(), "memory")
		if !platform.DirExists(memDir) {
			continue
		}

		// Check for .md files in memory/
		memFiles, err := os.ReadDir(memDir)
		if err != nil {
			continue
		}

		var mdFiles []string
		for _, mf := range memFiles {
			if !mf.IsDir() && strings.HasSuffix(mf.Name(), ".md") {
				content, err := os.ReadFile(filepath.Join(memDir, mf.Name()))
				if err == nil && len(strings.TrimSpace(string(content))) > 0 {
					mdFiles = append(mdFiles, mf.Name())
				}
			}
		}

		if len(mdFiles) == 0 {
			continue
		}

		// Derive a readable name and wing from the project directory name
		// Format: -home-hunter-code-brightspaces-node-auto-space-api
		projName := entry.Name()
		name := deriveProjectName(projName)
		wing := deriveWing(projName)

		projects = append(projects, memoryProject{
			name:      name,
			wing:      wing,
			memoryDir: memDir,
			files:     mdFiles,
		})
	}

	if len(projects) == 0 {
		output.Warn("No Claude Code memory files found to migrate")
		return
	}

	fmt.Println()
	output.Info(fmt.Sprintf("Found %d projects with memory files:", len(projects)))
	for _, p := range projects {
		fmt.Printf("    %-35s → wing: %s (%d files)\n", p.name, p.wing, len(p.files))
	}
	fmt.Println()

	migrated := 0
	failed := 0

	for _, p := range projects {
		output.Info(fmt.Sprintf("Migrating %s...", p.name))

		// Init the memory directory first (required before mine)
		if !platform.FileExists(filepath.Join(p.memoryDir, "mempalace.yaml")) {
			if err := runner.RunIndented("  ", mempalace, "--palace", dataDir, "init", p.memoryDir, "--yes"); err != nil {
				fmt.Println("  [x] init failed: " + err.Error())
				failed++
				continue
			}
		}

		// Mine the memory directory into the palace under the appropriate wing
		err := runner.RunIndented("  ", mempalace, "--palace", dataDir, "mine", p.memoryDir, "--wing", p.wing, "--no-gitignore")
		if err != nil {
			fmt.Println("  [x] failed: " + err.Error())
			failed++
			continue
		}
		fmt.Println("  [+] migrated")
		migrated++
	}

	// Summary
	fmt.Println()
	output.Header("═══ Migration Complete ═══")
	fmt.Printf("  Migrated:  %d\n", migrated)
	if failed > 0 {
		fmt.Printf("  Failed:    %d\n", failed)
	}
	fmt.Println()
	output.Info("Run 'mempalace status' to see what's been filed.")
	output.Info("Run 'mempalace search \"your query\"' to search migrated memories.")
}

// deriveProjectName extracts a human-readable name from the Claude project dir name.
// e.g. "-home-hunter-code-brightspaces-node-auto-space-api" → "brightspaces/node/auto-space-api"
func deriveProjectName(dirName string) string {
	// Strip the home path prefix pattern: -home-<user>-code-
	parts := strings.Split(dirName, "-")

	// Find "code" marker and take everything after it
	for i, part := range parts {
		if part == "code" && i+1 < len(parts) {
			remaining := strings.Join(parts[i+1:], "-")
			// Try to reconstruct path-like structure
			remaining = strings.Replace(remaining, "brightspaces-node-", "brightspaces/node/", 1)
			remaining = strings.Replace(remaining, "brightspaces-python-", "brightspaces/python/", 1)
			remaining = strings.Replace(remaining, "personal-", "personal/", 1)
			return remaining
		}
	}
	return dirName
}

// deriveWing determines which MemPalace wing a project belongs to.
func deriveWing(dirName string) string {
	lower := strings.ToLower(dirName)
	for pattern, wing := range projectMapping {
		if strings.Contains(lower, pattern) {
			return wing
		}
	}
	// Fall back to last meaningful segment
	parts := strings.Split(dirName, "-")
	for i, part := range parts {
		if part == "code" && i+1 < len(parts) {
			return parts[i+1]
		}
	}
	return "general"
}
