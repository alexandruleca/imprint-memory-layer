package cmd

import (
	"encoding/json"
	"fmt"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
	"os"
	"strings"
)

// Doctor reports installation health without invoking setup or changing profiles.
func Doctor(args []string) {
	project := platform.FindProjectDir()
	python := platform.VenvPython(project)
	if !platform.FileExists(python) {
		result := map[string]any{"ok": false, "checks": []any{map[string]any{"name": "python-runtime", "status": "attention", "detail": "Managed runtime is missing", "action": "Run imprint bootstrap --profile cpu --non-interactive"}}}
		if strings.Contains(" "+strings.Join(args, " ")+" ", " --json ") {
			data, _ := json.MarshalIndent(result, "", "  ")
			fmt.Println(string(data))
		} else {
			fmt.Fprintln(os.Stderr, "Managed runtime is missing. Run imprint bootstrap --profile cpu --non-interactive.")
		}
		os.Exit(1)
	}
	arguments := append([]string{"-m", "imprint.doctor"}, args...)
	command := runner.CommandWithEnv(python, arguments, "PYTHONPATH="+project, "IMPRINT_DATA_DIR="+platform.DataDir(project))
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if err := command.Run(); err != nil {
		os.Exit(1)
	}
}
