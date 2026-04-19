package cmd

import (
	"fmt"
	"os"
	"strings"

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

	if len(args) == 0 {
		fmt.Println("Usage: imprint ingest-url <url> [<url>...] [--project NAME] [--from-file urls.txt] [--force]")
		os.Exit(1)
	}

	lock := acquireOrEnqueue(dataDir, "ingest-url", args)
	defer lock.Release()

	envVars := []string{
		"PYTHONPATH=" + projectDir,
		"IMPRINT_DATA_DIR=" + dataDir,
	}

	// Translate the --from-file value when running under WSL2 and the user
	// pasted a Windows path. URL args are left alone — TranslateWSLPath
	// is a no-op for http(s):// strings.
	args = translateFromFileArg(args)

	pyArgs := append([]string{"-m", "imprint.cli_ingest_url"}, args...)
	cmd := runner.CommandWithEnv(venvPython, pyArgs, envVars...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		os.Exit(1)
	}
}

// translateFromFileArg walks args and passes the value that follows
// --from-file (either space-separated or --from-file=VALUE) through
// platform.TranslateWSLPath. Other args are untouched.
func translateFromFileArg(args []string) []string {
	out := make([]string, len(args))
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--from-file" && i+1 < len(args) {
			out[i] = a
			out[i+1] = platform.TranslateWSLPath(args[i+1])
			i++
			continue
		}
		if v, ok := strings.CutPrefix(a, "--from-file="); ok {
			out[i] = "--from-file=" + platform.TranslateWSLPath(v)
			continue
		}
		out[i] = a
	}
	return out
}

// RefreshURLs re-checks stored URLs via HEAD and re-indexes changed ones.
func RefreshURLs(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	lock := acquireOrEnqueue(dataDir, "refresh-urls", args)
	defer lock.Release()

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
