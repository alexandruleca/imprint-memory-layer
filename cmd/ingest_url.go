package cmd

import (
	"fmt"
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// IngestURL fetches one or more URLs, extracts text, chunks, embeds, and
// stores. Supports --from-file, --project, --force flags (parsed by the
// Python module — we pass through).
func IngestURL(args []string) {
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

	if len(args) == 0 {
		fmt.Println("Usage: imprint ingest-url <url> [<url>...] [--project NAME] [--from-file urls.txt] [--force]")
		os.Exit(1)
	}

	pyArgs := append([]string{"-m", "imprint.cli_ingest_url"}, args...)
	cmd := runner.CommandWithEnv(venvPython, pyArgs, envVars...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		os.Exit(1)
	}
}

// RefreshURLs re-checks stored URLs via HEAD and re-indexes changed ones.
func RefreshURLs(args []string) {
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

	pyArgs := append([]string{"-m", "imprint.cli_refresh_urls"}, args...)
	cmd := runner.CommandWithEnv(venvPython, pyArgs, envVars...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		os.Exit(1)
	}
}
