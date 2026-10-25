//go:build !windows

package hooks

import "os/exec"

func hookShellCommand(command string) *exec.Cmd {
	return exec.Command("sh", "-c", command)
}

// copilotShellCommand runs a command the way Copilot runs a hook outside Windows.
func copilotShellCommand(command string) *exec.Cmd {
	return exec.Command("bash", "-c", command)
}
