//go:build windows

package queuelock

import (
	"fmt"
	"syscall"
	"unsafe"
)

// Windows LockFileEx flags (not exposed by the stdlib syscall package).
const (
	lockfileExclusiveLock   = 0x00000002
	lockfileFailImmediately = 0x00000001
)

var (
	modkernel32          = syscall.NewLazyDLL("kernel32.dll")
	procLockFileEx       = modkernel32.NewProc("LockFileEx")
	procUnlockFileEx     = modkernel32.NewProc("UnlockFileEx")
	procSetFilePointerEx = modkernel32.NewProc("SetFilePointerEx")
	procSetEndOfFile     = modkernel32.NewProc("SetEndOfFile")
)

// Handle owns a held queue lock. Windows impl: HANDLE + LockFileEx.
type Handle struct {
	fd       syscall.Handle
	path     string
	released bool
}

func acquireNative(path string, body []byte) (*Handle, error) {
	namePtr, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return nil, fmt.Errorf("convert path: %w", err)
	}
	fd, err := syscall.CreateFile(
		namePtr,
		syscall.GENERIC_READ|syscall.GENERIC_WRITE,
		syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE,
		nil,
		syscall.OPEN_ALWAYS,
		syscall.FILE_ATTRIBUTE_NORMAL,
		0,
	)
	if err != nil {
		return nil, fmt.Errorf("open lock file: %w", err)
	}

	var overlapped syscall.Overlapped
	r1, _, e1 := procLockFileEx.Call(
		uintptr(fd),
		uintptr(lockfileExclusiveLock|lockfileFailImmediately),
		0,
		0xFFFFFFFF, 0xFFFFFFFF,
		uintptr(unsafe.Pointer(&overlapped)),
	)
	if r1 == 0 {
		_ = syscall.CloseHandle(fd)
		_ = e1
		return nil, &busySentinel{}
	}

	// Seek to 0, truncate, write holder body.
	var newPos int64
	_, _, _ = procSetFilePointerEx.Call(uintptr(fd), 0, uintptr(unsafe.Pointer(&newPos)), 0)
	_, _, _ = procSetEndOfFile.Call(uintptr(fd))
	var written uint32
	_ = syscall.WriteFile(fd, body, &written, nil)
	_ = syscall.FlushFileBuffers(fd)

	return &Handle{fd: fd, path: path}, nil
}

func releaseNative(h *Handle) {
	// Clear the holder metadata first, so nobody reads it as still held.
	var newPos int64
	_, _, _ = procSetFilePointerEx.Call(uintptr(h.fd), 0, uintptr(unsafe.Pointer(&newPos)), 0)
	_, _, _ = procSetEndOfFile.Call(uintptr(h.fd))
	var overlapped syscall.Overlapped
	_, _, _ = procUnlockFileEx.Call(
		uintptr(h.fd),
		0,
		0xFFFFFFFF, 0xFFFFFFFF,
		uintptr(unsafe.Pointer(&overlapped)),
	)
	_ = syscall.CloseHandle(h.fd)
}

const (
	processTerminate               = 0x0001
	processQueryLimitedInformation = 0x1000
	stillActive                    = 259
	// Exit code of a preempted holder: Python's os.kill(pid, SIGTERM) on
	// Windows terminates with the signal number, and the dispatcher reads
	// 15 as "stopped to make way" (ADR 0067).
	preemptedExitCode = 15
)

func processAlive(pid int) bool {
	h, err := syscall.OpenProcess(processQueryLimitedInformation, false, uint32(pid))
	if err != nil {
		return err == syscall.ERROR_ACCESS_DENIED
	}
	defer syscall.CloseHandle(h)
	var code uint32
	if err := syscall.GetExitCodeProcess(h, &code); err != nil {
		return false
	}
	return code == stillActive
}

// stopProcess terminates the process; Windows has no gentler signal for it.
func stopProcess(pid int, _ bool) {
	h, err := syscall.OpenProcess(processTerminate, false, uint32(pid))
	if err != nil {
		return
	}
	defer syscall.CloseHandle(h)
	_ = syscall.TerminateProcess(h, preemptedExitCode)
}
