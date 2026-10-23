package cmd

import (
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
	"os"
)

func Device(args []string) {
	project := platform.FindProjectDir()
	python := platform.VenvPython(project)
	child := runner.CommandWithEnv(python, append([]string{"-m", "imprint.cli_device"}, args...),
		"PYTHONPATH="+project, "IMPRINT_DATA_DIR="+platform.DataDir(project))
	child.Stdout, child.Stderr = os.Stdout, os.Stderr
	if err := child.Run(); err != nil {
		output.Fail("Device context failed: " + err.Error())
	}
}
