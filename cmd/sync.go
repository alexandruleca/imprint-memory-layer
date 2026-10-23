package cmd

import (
	"os"
	"os/signal"
	"syscall"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Sync runs `imprint sync` in Python (imprint/cli_sync.py), where the one
// implementation of the sync protocol lives; the dashboard drives the same
// sessions. The relay (`imprint relay`) stays in Go.
//
//	imprint sync serve [--relay <host>] [--workspace <name>] [--send-only | --receive-only]
//	imprint sync <code> [--workspace <name>] [--two-way | --send-only] [--fresh]
//	imprint sync export | import ...
func Sync(args []string) {
	if len(args) == 0 {
		args = []string{"--help"}
	} else {
		switch args[0] {
		case "serve", "export", "import", "watch", "peers", "enable", "disable", "forget", "-h", "--help":
		default:
			args = append([]string{"join"}, args...)
		}
	}

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	if !platform.FileExists(venvPython) {
		output.Fail("Run 'imprint setup' first")
	}

	child := runner.CommandWithEnv(venvPython, append([]string{"-m", "imprint.cli_sync"}, args...),
		"PYTHONPATH="+projectDir,
		"IMPRINT_DATA_DIR="+platform.DataDir(projectDir),
	)
	child.Stdin, child.Stdout, child.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := child.Start(); err != nil {
		output.Fail("Cannot start sync: " + err.Error())
	}

	// Ctrl+C reaches the whole foreground process group, so Python sees it
	// and disconnects cleanly; catching it here keeps this process alive to
	// report the exit code. SIGTERM (e.g. from a service manager) is aimed at
	// this process alone, so it is passed on.
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)
	go func() {
		for sig := range signals {
			if sig == syscall.SIGTERM {
				_ = child.Process.Signal(sig)
			}
		}
	}()

	_ = child.Wait()
	code := child.ProcessState.ExitCode()
	if code < 0 { // killed by a signal
		code = 1
	}
	os.Exit(code)
}
