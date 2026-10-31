// Package testtime stretches test deadlines to what the machine running them
// needs. CI runs on busy hosts, Windows and macOS VMs, and an arm64 runner
// under qemu-user emulation several times slower (ADR 0112), so a deadline
// that is ample locally fails there now and then. tests/emulation.py and
// imprint/ui/tests/slack.ts apply the same allowance to the Python and
// Playwright tests. Only tests import this package.
package testtime

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Scale is IMPRINT_TEST_TIME_SCALE if set, else 5 under qemu emulation,
// else 3 on CI (CI set), else 1.
var Scale = sync.OnceValue(func() float64 {
	if value := os.Getenv("IMPRINT_TEST_TIME_SCALE"); value != "" {
		if scale, err := strconv.ParseFloat(value, 64); err == nil && scale > 0 {
			return scale
		}
	}
	if emulated() {
		return 5
	}
	if os.Getenv("CI") != "" {
		return 3
	}
	return 1
})

// Slack is d, stretched to what this machine needs to reliably finish in it.
func Slack(d time.Duration) time.Duration {
	return time.Duration(float64(d) * Scale())
}

// emulated reports whether this runs under qemu-user. QEMU fakes
// /proc/cpuinfo but not another process's /proc/<pid>/exe, which names
// the emulator.
func emulated() bool {
	if runtime.GOOS != "linux" {
		return false
	}
	child := exec.Command("sleep", "5")
	if child.Start() != nil {
		return false
	}
	defer func() {
		_ = child.Process.Kill()
		_ = child.Wait()
	}()
	exe, err := os.Readlink("/proc/" + strconv.Itoa(child.Process.Pid) + "/exe")
	return err == nil && strings.HasPrefix(filepath.Base(exe), "qemu-")
}
