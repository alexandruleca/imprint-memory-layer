//go:build windows

package cmd

import (
	"os"
	"syscall"
)

const (
	createNewProcessGroup = 0x00000200
	createNoWindow        = 0x08000000
)

// detachAttrs gives a console program a console of its own without a window,
// so it outlives this CLI's console. Console programs it starts (`imprint ui
// serve` starts Python) inherit that console; under DETACHED_PROCESS each
// would open a window. GUI programs ignore the flag.
func detachAttrs() *syscall.SysProcAttr {
	return &syscall.SysProcAttr{CreationFlags: createNoWindow | createNewProcessGroup}
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
