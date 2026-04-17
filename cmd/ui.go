package cmd

import (
	"os"
	"os/signal"
	"syscall"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

func UI(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	pyArgs := []string{"-m", "imprint.api", "--auto-shutdown"}
	pyArgs = append(pyArgs, args...)

	cmd := runner.CommandWithEnv(venvPython, pyArgs,
		"PYTHONPATH="+projectDir,
		"IMPRINT_DATA_DIR="+dataDir,
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	if err := cmd.Start(); err != nil {
		output.Fail("Failed to start server: " + err.Error())
	}

	// Forward interrupt signals to the Python subprocess so uvicorn
	// can shut down gracefully instead of being orphaned.
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigCh
		if cmd.Process != nil {
			cmd.Process.Signal(os.Interrupt)
		}
	}()

	cmd.Wait()
}
