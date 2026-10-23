package hooks

import (
	"os/exec"
	"syscall"
)

func hookShellCommand(command string) *exec.Cmd {
	// cmd.exe parses its /c tail itself. Go's default CRT argument escaping
	// inserts literal backslashes before these quotes; use cmd's raw syntax.
	child := exec.Command("cmd.exe")
	child.SysProcAttr = &syscall.SysProcAttr{CmdLine: `cmd.exe /d /s /c "` + command + `"`}
	return child
}
