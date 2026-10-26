//go:build windows

package cmd

import (
	"os"
	"syscall"
)

// stdinIsTerminal reports whether standard input is a console a person types in.
func stdinIsTerminal() bool {
	var mode uint32
	return syscall.GetConsoleMode(syscall.Handle(os.Stdin.Fd()), &mode) == nil
}
