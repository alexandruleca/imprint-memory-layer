package cmd

import (
	"os"
	"os/exec"

	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
)

// Passthrough forwards a command to mempalace with --palace <dataDir> injected.
func Passthrough(args []string) {
	projectDir := platform.FindProjectDir()
	mempalace := platform.VenvBin(projectDir, "mempalace")
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(mempalace) {
		output.Fail("mempalace not found — run 'knowledge setup' first")
	}

	// Build: mempalace --palace <dataDir> <subcommand> [args...]
	cmdArgs := append([]string{"--palace", dataDir}, args...)

	cmd := exec.Command(mempalace, cmdArgs...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	if err := cmd.Run(); err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			os.Exit(exitErr.ExitCode())
		}
		os.Exit(1)
	}
}
