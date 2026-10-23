package queuelock

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

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
