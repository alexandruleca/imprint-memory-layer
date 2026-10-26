package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"time"

	"github.com/hunter/imprint/internal/output"
)

// processInfo is a running process as the OS reports it. Cmdline separates
// arguments with NUL where the OS keeps them apart (Linux), else spaces.
type processInfo struct {
	PID     int
	PPID    int
	Exe     string
	Cmdline string
}

// ownedBy reports whether p belongs to this installation: it runs a file
// from one of roots, or it is the desktop shell `imprint ui open` started
// for project (Windows and Linux run that shell from a temporary copy).
// Programs Imprint merely opened, such as a browser, match neither.
func ownedBy(p processInfo, roots []string, project string) bool {
	if p.Exe != "" {
		for _, root := range roots {
			if root != "" && pathWithin(p.Exe, root) {
				return true
			}
		}
	}
	return project != "" && hasArgument(p.Cmdline, "--project-dir="+project)
}

func foldPath(path string) string {
	path = filepath.Clean(path)
	if runtime.GOOS == "windows" || runtime.GOOS == "darwin" {
		return strings.ToLower(path)
	}
	return path
}

func pathWithin(path, root string) bool {
	rel, err := filepath.Rel(foldPath(root), foldPath(path))
	return err == nil && rel != "." && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

// hasArgument finds arg as a whole argument: the match must end where the
// argument does, so /opt/imprint does not match /opt/imprint2.
func hasArgument(cmdline, arg string) bool {
	if runtime.GOOS == "windows" || runtime.GOOS == "darwin" {
		cmdline, arg = strings.ToLower(cmdline), strings.ToLower(arg)
	}
	for start := 0; ; {
		i := strings.Index(cmdline[start:], arg)
		if i < 0 {
			return false
		}
		end := start + i + len(arg)
		if end == len(cmdline) || strings.ContainsRune("\x00 \t\"", rune(cmdline[end])) {
			return true
		}
		start = end
	}
}

// stopOwnedProcesses ends every process of this installation except this
// one and except, so its files can be deleted or replaced: a polite stop
// first, then a forced one. Run it after clients are disconnected, or they
// may start servers again.
func stopOwnedProcesses(roots []string, project string, except ...int) error {
	except = append(except, os.Getpid())
	owned := func() ([]processInfo, error) {
		all, err := listProcesses()
		if err != nil {
			return nil, fmt.Errorf("listing processes: %w", err)
		}
		var found []processInfo
		for _, p := range all {
			if !slices.Contains(except, p.PID) && ownedBy(p, roots, project) {
				found = append(found, p)
			}
		}
		return found, nil
	}
	waitGone := func(timeout time.Duration) ([]processInfo, error) {
		deadline := time.Now().Add(timeout)
		for {
			left, err := owned()
			if err != nil || len(left) == 0 || time.Now().After(deadline) {
				return left, err
			}
			time.Sleep(200 * time.Millisecond)
		}
	}

	procs, err := owned()
	if err != nil || len(procs) == 0 {
		return err
	}
	for _, p := range procs {
		output.Info(fmt.Sprintf("Stopping %s (pid %d)", processLabel(p), p.PID))
		_ = stopProcess(p.PID)
	}
	if procs, err = waitGone(10 * time.Second); err != nil || len(procs) == 0 {
		return err
	}
	for _, p := range procs {
		_ = killProcess(p.PID)
	}
	if procs, err = waitGone(5 * time.Second); err != nil || len(procs) == 0 {
		return err
	}
	var names []string
	for _, p := range procs {
		names = append(names, fmt.Sprintf("%s (pid %d)", processLabel(p), p.PID))
	}
	return fmt.Errorf("these Imprint processes would not stop: %s", strings.Join(names, ", "))
}

// withLauncher is pid and, when it also runs from one of roots, its parent:
// on Windows a venv's python.exe is a launcher that runs the interpreter as
// its child and exits with it.
func withLauncher(pid int, roots []string) []int {
	all, _ := listProcesses()
	for _, p := range all {
		if p.PID != pid {
			continue
		}
		for _, parent := range all {
			if parent.PID == p.PPID && ownedBy(parent, roots, "") {
				return []int{pid, parent.PID}
			}
		}
	}
	return []int{pid}
}

// waitForExit reports whether every process in pids exited within timeout.
func waitForExit(pids []int, timeout time.Duration) bool {
	for deadline := time.Now().Add(timeout); ; time.Sleep(200 * time.Millisecond) {
		all, err := listProcesses()
		if err == nil && !slices.ContainsFunc(all, func(p processInfo) bool { return slices.Contains(pids, p.PID) }) {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
	}
}

func processLabel(p processInfo) string {
	if p.Exe != "" {
		return filepath.Base(p.Exe)
	}
	return "desktop app"
}
