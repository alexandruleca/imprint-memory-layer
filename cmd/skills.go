package cmd

import (
	"os"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Skills manages the user's skills and personas (ADRs 0168, 0169): list,
// show, add, found, adopt, enable, disable, remove and apply, implemented by
// imprint.cli_skills.
func Skills(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}
	child := runner.CommandWithEnv(venvPython, append([]string{"-m", "imprint.cli_skills"}, args...),
		"PYTHONPATH="+projectDir, "IMPRINT_DATA_DIR="+platform.DataDir(projectDir))
	child.Stdout, child.Stderr, child.Stdin = os.Stdout, os.Stderr, os.Stdin
	if err := child.Run(); err != nil {
		if exit, ok := err.(interface{ ExitCode() int }); ok && exit.ExitCode() > 0 {
			os.Exit(exit.ExitCode())
		}
		output.Fail("imprint skills failed: " + err.Error())
	}
}

// applySkills writes the registry's skills and personas into the folders
// the connected clients read, after connect or disconnect changed which
// clients those are. A runtime that is not installed, or a registry that
// holds nothing and wrote nothing, is left alone; a failure only warns,
// since the clients' registration itself succeeded.
func applySkills(venvPython, projectDir, dataDir string) {
	if !platform.FileExists(venvPython) {
		return
	}
	child := runner.CommandWithEnv(venvPython, []string{"-m", "imprint.cli_skills", "apply", "--quiet", "--if-needed"},
		"PYTHONPATH="+projectDir, "IMPRINT_DATA_DIR="+dataDir)
	child.Stdout, child.Stderr = os.Stdout, os.Stderr
	if err := child.Run(); err != nil {
		output.Warn("Skills and personas were not written to the clients: " + err.Error())
	}
}
