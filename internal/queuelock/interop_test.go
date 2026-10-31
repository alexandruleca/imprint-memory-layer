package queuelock

import (
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/hunter/imprint/internal/testtime"
)

// The exit code a holder stopped on Windows reports; Unix holders end by SIGTERM.
const preemptedExitCodeForTest = 15

func TestPythonContendsWithGoLock(t *testing.T) {
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	interpreter, err := exec.LookPath(python)
	if err != nil {
		t.Skip("Python not available")
	}
	directory := t.TempDir()
	handle, err := TryAcquire(directory, "interop")
	if err != nil {
		t.Fatal(err)
	}
	defer handle.Release()
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	probe := exec.Command(interpreter, "-c", `import os,sys
from imprint.queue_lock import _lock
fd=os.open(sys.argv[1],os.O_RDWR)
try:
    acquired=_lock(fd,False)
    assert not acquired, 'Python bypassed the Go queue lock'
finally:
    os.close(fd)
`, filepath.Join(directory, "queue.lock"))
	probe.Env = append(os.Environ(), "PYTHONPATH="+root)
	if output, err := probe.CombinedOutput(); err != nil {
		t.Fatalf("Python lock probe: %v: %s", err, output)
	}
}

// pythonHolder starts a Python process that takes the lock the way the
// dashboard's background tagging job does (preemptible) or a purge does.
func pythonHolder(t *testing.T, directory string, preemptible bool) *exec.Cmd {
	t.Helper()
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	interpreter, err := exec.LookPath(python)
	if err != nil {
		t.Skip("Python not available")
	}
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	flag := "False"
	if preemptible {
		flag = "True"
	}
	holder := exec.Command(interpreter, "-c", `import sys, time
from imprint import queue_lock
descriptor = queue_lock.acquire('tag-backlog', 'job', preemptible=`+flag+`)
print('held' if descriptor is not None else 'busy', flush=True)
time.sleep(60)
`)
	holder.Env = append(os.Environ(), "PYTHONPATH="+root, "IMPRINT_DATA_DIR="+directory)
	stdout, err := holder.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := holder.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = holder.Process.Kill(); _ = holder.Wait() })
	line := make([]byte, 5)
	if _, err := io.ReadFull(stdout, line[:4]); err != nil || string(line[:4]) != "held" {
		t.Fatalf("Python holder did not take the lock: %q %v", line, err)
	}
	return holder
}

func TestTryAcquireStopsAPreemptibleHolder(t *testing.T) {
	directory := t.TempDir()
	holder := pythonHolder(t, directory, true)
	if h, _ := ReadHolder(directory); h == nil || !h.Preemptible || h.Command != "tag-backlog" {
		t.Fatalf("the Python holder's metadata is not readable as preemptible: %+v", h)
	}
	start := time.Now()
	handle, err := TryAcquire(directory, "update")
	if err != nil {
		t.Fatalf("TryAcquire did not take the lock from a preemptible holder: %v", err)
	}
	defer handle.Release()
	if time.Since(start) > preemptWait {
		t.Fatalf("taking the lock took %s", time.Since(start))
	}
	done := make(chan error, 1)
	go func() { done <- holder.Wait() }()
	select {
	case <-done:
	case <-time.After(testtime.Slack(10 * time.Second)):
		t.Fatal("the preempted holder kept running")
	}
	if code := holder.ProcessState.ExitCode(); runtime.GOOS == "windows" && code != preemptedExitCodeForTest {
		t.Fatalf("preempted holder exited with %d, want %d", code, preemptedExitCodeForTest)
	}
}

func TestTryAcquireLeavesOtherHoldersRunning(t *testing.T) {
	directory := t.TempDir()
	holder := pythonHolder(t, directory, false)
	_, err := TryAcquire(directory, "update")
	var busy *BusyError
	if !errors.As(err, &busy) {
		t.Fatalf("TryAcquire took the lock from a holder that is not preemptible: %v", err)
	}
	if holder.ProcessState != nil {
		t.Fatal("the holder was stopped")
	}
}

func TestReleaseClearsTheHolderMetadata(t *testing.T) {
	directory := t.TempDir()
	handle, err := TryAcquire(directory, "ingest")
	if err != nil {
		t.Fatal(err)
	}
	handle.Release()
	if h, err := ReadHolder(directory); err == nil {
		t.Fatalf("a released lock still names its holder: %+v", h)
	}
}

func TestReleaseTwiceLeavesALaterHolderAlone(t *testing.T) {
	directory := t.TempDir()
	first, err := TryAcquire(directory, "update")
	if err != nil {
		t.Fatal(err)
	}
	first.Release()
	second, err := TryAcquire(directory, "setup")
	if err != nil {
		t.Fatal(err)
	}
	defer second.Release()
	first.Release() // The second lock may reuse the first one's descriptor.
	if third, err := TryAcquire(directory, "ingest"); err == nil {
		third.Release()
		t.Fatal("releasing a released handle released the next holder's lock")
	}
}

// The dashboard server waits in Python while setup holds the runtime lock (ADR 0084).
func TestPythonWaitsForTheRuntimeLock(t *testing.T) {
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	interpreter, err := exec.LookPath(python)
	if err != nil {
		t.Skip("Python not available")
	}
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	directory := t.TempDir()
	handle, err := HoldRuntime(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer handle.Release()
	waiter := exec.Command(interpreter, "-c", "from imprint.queue_lock import wait_for_runtime; wait_for_runtime()")
	waiter.Env = append(os.Environ(), "PYTHONPATH="+root, "IMPRINT_DATA_DIR="+directory)
	if err := waiter.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- waiter.Wait() }()
	select {
	case err := <-done:
		t.Fatalf("Python did not wait while setup held the runtime lock: %v", err)
	case <-time.After(time.Second):
	}
	handle.Release()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("waiting failed: %v", err)
		}
	case <-time.After(testtime.Slack(10 * time.Second)):
		t.Fatal("Python kept waiting after setup released the runtime lock")
	}
	if err := WaitRuntime(directory); err != nil {
		t.Fatal(err)
	}
}
