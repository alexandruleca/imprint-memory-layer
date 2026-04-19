//go:build unix

package queuelock

import (
	"fmt"
	"syscall"
)

// Handle owns a held queue lock. Unix impl: posix fd + fcntl flock.
type Handle struct {
	fd   int
	path string
}

func acquireNative(path string, body []byte) (*Handle, error) {
	fd, err := syscall.Open(path, syscall.O_RDWR|syscall.O_CREAT, 0o644)
	if err != nil {
		return nil, fmt.Errorf("open lock file: %w", err)
	}
	if err := syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = syscall.Close(fd)
		return nil, &busySentinel{}
	}
	_ = syscall.Ftruncate(fd, 0)
	_, _ = syscall.Seek(fd, 0, 0)
	_, _ = syscall.Write(fd, body)
	_ = syscall.Fsync(fd)
	return &Handle{fd: fd, path: path}, nil
}

func releaseNative(h *Handle) {
	_ = syscall.Flock(h.fd, syscall.LOCK_UN)
	_ = syscall.Close(h.fd)
}
