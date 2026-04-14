package cmd

import (
	"fmt"
	"os"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Config dispatches config subcommands.
//
//	imprint config              — list all settings
//	imprint config list         — list all settings
//	imprint config get <key>    — show one setting
//	imprint config set <k> <v>  — persist a setting
//	imprint config reset <key>  — remove override
//	imprint config reset --all  — clear all overrides
func Config(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("run 'imprint setup' first")
	}

	envVars := []string{
		"PYTHONPATH=" + projectDir,
		"IMPRINT_DATA_DIR=" + dataDir,
	}

	runPy := func(code string) {
		out, err := runner.RunCaptureEnv(venvPython, envVars, "-c", code)
		out = strings.TrimRight(out, "\n")
		if out != "" {
			fmt.Println(out)
		}
		if err != nil {
			os.Exit(1)
		}
	}

	if len(args) == 0 {
		runPy("from imprint.config_schema import cli_list; cli_list()")
		return
	}

	switch args[0] {
	case "list":
		runPy("from imprint.config_schema import cli_list; cli_list()")

	case "get":
		if len(args) < 2 {
			output.Fail("usage: imprint config get <key>")
		}
		runPy(fmt.Sprintf("from imprint.config_schema import cli_get; cli_get(%q)", args[1]))

	case "set":
		if len(args) < 3 {
			output.Fail("usage: imprint config set <key> <value>")
		}
		// Join remaining args as value (allows spaces in values)
		value := strings.Join(args[2:], " ")
		runPy(fmt.Sprintf("from imprint.config_schema import cli_set; cli_set(%q, %q)", args[1], value))

	case "reset":
		if len(args) < 2 {
			output.Fail("usage: imprint config reset <key>  or  imprint config reset --all")
		}
		if args[1] == "--all" {
			runPy("from imprint.config_schema import cli_reset_all; cli_reset_all()")
		} else {
			runPy(fmt.Sprintf("from imprint.config_schema import cli_reset; cli_reset(%q)", args[1]))
		}

	default:
		fmt.Fprintf(os.Stderr, `usage: imprint config [list|get|set|reset]

  imprint config              List all settings with current values
  imprint config get <key>    Show one setting
  imprint config set <k> <v>  Persist a setting
  imprint config reset <key>  Remove override, revert to default
  imprint config reset --all  Clear all overrides

Examples:
  imprint config set model.name nomic-ai/nomic-embed-text-v2-moe
  imprint config set model.dim 768
  imprint config set tagger.llm_provider ollama
  imprint config get model.name
  imprint config reset model.name
`)
		os.Exit(1)
	}
}
