//go:build !windows

package cmd

import (
	"os"
	"syscall"
)

// detachAttrs returns SysProcAttr so the child process becomes its own
// session leader and survives the CLI exiting.
func detachAttrs() *syscall.SysProcAttr {
	return &syscall.SysProcAttr{Setsid: true}
}

// stopProcess sends SIGTERM so the FastAPI/uvicorn shutdown handlers run.
func stopProcess(pid int) error {
	p, err := os.FindProcess(pid)
	if err != nil {
		return err
	}
	if err := p.Signal(syscall.SIGTERM); err != nil {
		return err
	}
	return nil
}
