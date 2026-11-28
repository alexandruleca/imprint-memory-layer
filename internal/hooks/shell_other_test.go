//go:build !windows

package hooks

import (
	"os/exec"
	"strings"
	"testing"
)

func hookShellCommand(command string) *exec.Cmd {
	return exec.Command("sh", "-c", command)
}

// copilotShellCommand runs a command the way Copilot runs a hook outside Windows.
func copilotShellCommand(command string) *exec.Cmd {
	return exec.Command("bash", "-c", command)
}

// cursorShellCommand runs a hook the way Cursor does outside Windows: the
// command in sh, the payload on its standard input.
func cursorShellCommand(t *testing.T, command, payload string) *exec.Cmd {
	child := exec.Command("sh", "-c", command)
	child.Stdin = strings.NewReader(payload)
	return child
}
