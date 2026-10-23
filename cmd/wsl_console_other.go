//go:build !windows

package cmd

import "os/exec"

func hideWSLConsole(child *exec.Cmd) {}
