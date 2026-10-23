//go:build windows

package cmd

import (
	"os/exec"
	"syscall"
)

func hideWSLConsole(child *exec.Cmd) {
	// WSL loses redirected stdout with DETACHED_PROCESS. CREATE_NO_WINDOW
	// suppresses a console while preserving the profile-discovery pipes.
	child.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
}
