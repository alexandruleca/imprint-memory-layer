package cmd

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
)

func TestDeviceIdentityIsSharedByConcurrentGoAndPython(t *testing.T) {
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	interpreter, err := exec.LookPath(python)
	if err != nil {
		t.Skip("Python required for device identity interoperability")
	}
	directory := t.TempDir()
	project, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	child := exec.Command(interpreter, "-c", "from imprint.device import current; print(current()['id'])")
	child.Env = append(os.Environ(), "PYTHONPATH="+project, "IMPRINT_DATA_DIR="+directory)
	var stdout, stderr bytes.Buffer
	child.Stdout, child.Stderr = &stdout, &stderr
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	ids := make(chan string, 20)
	var workers sync.WaitGroup
	for range 20 {
		workers.Add(1)
		go func() { defer workers.Done(); ids <- loadOrCreateFingerprint(directory) }()
	}
	workers.Wait()
	close(ids)
	if err := child.Wait(); err != nil {
		t.Fatalf("Python identity: %v: %s", err, stderr.String())
	}
	want := strings.TrimSpace(stdout.String())
	for id := range ids {
		if id != want {
			t.Fatalf("Device identity split: Go %q, Python %q", id, want)
		}
	}
}
