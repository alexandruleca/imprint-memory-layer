//go:build !windows

package hooks

import "os/exec"

func hookShellCommand(command string) *exec.Cmd {
	return exec.Command("sh", "-c", command)
}
