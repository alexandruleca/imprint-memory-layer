//go:build linux

package cmd

import (
	"os"
	"syscall"
	"unsafe"
)

// stdinIsTerminal reports whether standard input is a terminal a person types
// in: one that answers a terminal-attributes request, which /dev/null and
// pipes do not.
func stdinIsTerminal() bool {
	var attributes syscall.Termios
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, os.Stdin.Fd(), syscall.TCGETS, uintptr(unsafe.Pointer(&attributes)))
	return errno == 0
}
