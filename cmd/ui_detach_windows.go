//go:build windows

package cmd

import (
	"os"
	"syscall"
)

const (
	detachedProcess       = 0x00000008
	createNewProcessGroup = 0x00000200
)

func detachAttrs() *syscall.SysProcAttr {
	return &syscall.SysProcAttr{CreationFlags: detachedProcess | createNewProcessGroup}
}

func stopProcess(pid int) error {
	p, err := os.FindProcess(pid)
	if err != nil {
		return err
	}
	return p.Kill()
}

// killProcess is stopProcess: Windows has no polite signal for console and
// background processes, and closing the desktop window only hides it.
func killProcess(pid int) error { return stopProcess(pid) }
