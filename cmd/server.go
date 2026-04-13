package cmd

import (
	"fmt"
	"os"

	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
	"github.com/hunter/knowledge/internal/runner"
)

// Server manages the auto-spawned local Qdrant daemon. Subcommands:
//   knowledge server start   — ensure server running (downloads binary on first call)
//   knowledge server stop    — terminate the daemon
//   knowledge server status  — print pid + reachability
//   knowledge server log     — tail the server log
func Server(args []string) {
	if len(args) == 0 {
		fmt.Fprintf(os.Stderr, `Usage:
  knowledge server start    Start (or wake) the local Qdrant server
  knowledge server stop     Terminate the running daemon
  knowledge server status   Show pid + reachability
  knowledge server log      Print path to the server log file
`)
		os.Exit(1)
	}

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)
	if !platform.FileExists(venvPython) {
		output.Fail("Run 'knowledge setup' first")
	}

	envVars := []string{
		"PYTHONPATH=" + projectDir,
		"KNOWLEDGE_DATA_DIR=" + dataDir,
	}

	switch args[0] {
	case "start":
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", `from knowledgebase import qdrant_runner as q; h,p=q.ensure_running(); print(f"running at {h}:{p}")`)
		if err != nil {
			output.Fail("server start failed: " + err.Error() + "\n" + out)
		}
		output.Success(out)
	case "stop":
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", `from knowledgebase import qdrant_runner as q; print("stopped" if q.stop() else "not running")`)
		if err != nil {
			output.Fail("server stop failed: " + err.Error())
		}
		output.Info(out)
	case "status":
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", `import json; from knowledgebase import qdrant_runner as q; print(json.dumps(q.status(), indent=2))`)
		if err != nil {
			output.Fail("status failed: " + err.Error())
		}
		fmt.Println(out)
	case "log":
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", `from knowledgebase import qdrant_runner as q; print(q._log_file())`)
		if err != nil {
			output.Fail("log lookup failed: " + err.Error())
		}
		output.Info("tail -f " + out)
	default:
		output.Fail("unknown server subcommand: " + args[0])
	}
}
