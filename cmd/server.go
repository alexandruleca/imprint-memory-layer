package cmd

import (
	"fmt"
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Server manages the auto-spawned local Qdrant daemon. Subcommands:
//   imprint server start   — ensure server running (downloads binary on first call)
//   imprint server stop    — terminate the daemon
//   imprint server status  — print pid + reachability
//   imprint server log     — tail the server log
func Server(args []string) {
	if len(args) == 0 {
		fmt.Fprintf(os.Stderr, `Usage:
  imprint server start    Start (or wake) the local Qdrant server
  imprint server stop     Terminate the running daemon
  imprint server status   Show pid + reachability
  imprint server log      Print path to the server log file
`)
		os.Exit(1)
	}

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)
	if !platform.FileExists(venvPython) {
		output.Fail("Run 'imprint setup' first")
	}

	envVars := []string{
		"PYTHONPATH=" + projectDir,
		"IMPRINT_DATA_DIR=" + dataDir,
	}

	switch args[0] {
	case "start":
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", `from imprint import qdrant_runner as q; h,p=q.ensure_running(); print(f"running at {h}:{p}")`)
		if err != nil {
			output.Fail("server start failed: " + err.Error() + "\n" + out)
		}
		output.Success(out)
	case "stop":
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", `from imprint import qdrant_runner as q; print("stopped" if q.stop() else "not running")`)
		if err != nil {
			output.Fail("server stop failed: " + err.Error())
		}
		output.Info(out)
	case "status":
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", `import json; from imprint import qdrant_runner as q; print(json.dumps(q.status(), indent=2))`)
		if err != nil {
			output.Fail("status failed: " + err.Error())
		}
		fmt.Println(out)
	case "log":
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", `from imprint import qdrant_runner as q; print(q._log_file())`)
		if err != nil {
			output.Fail("log lookup failed: " + err.Error())
		}
		output.Info("tail -f " + out)
	default:
		output.Fail("unknown server subcommand: " + args[0])
	}
}
