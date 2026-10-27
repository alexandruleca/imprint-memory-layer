package queuelock

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestProgressIsReadWhileItsWriterRunsAndClearedByIt(t *testing.T) {
	dir := t.TempDir()
	if err := WriteProgress(dir, Progress{Command: "setup", Phase: "installing llama.cpp", Processed: 1, Total: 3, Unit: "steps"}); err != nil {
		t.Fatal(err)
	}
	p, ok := ReadProgress(dir)
	if !ok || p.PID != os.Getpid() || p.Command != "setup" || p.Total != 3 || p.UpdatedAt == 0 || p.Projects == nil {
		t.Fatalf("ReadProgress = %+v, %v", p, ok)
	}
	if got, want := p.String(), "setup: installing llama.cpp, 1/3 steps"; got != want {
		t.Errorf("String() = %q, want %q", got, want)
	}
	ClearProgress(dir)
	if _, ok := ReadProgress(dir); ok {
		t.Error("the record outlived ClearProgress")
	}
}

func TestProgressOfAnEndedProcessOrAnotherIsNotOurs(t *testing.T) {
	dir := t.TempDir()
	write := func(pid int) {
		data, _ := json.Marshal(Progress{PID: pid, Command: "learn", Phase: "reading", Unit: "conversations"})
		if err := os.WriteFile(filepath.Join(dir, "progress.json"), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write(1 << 30) // No such process.
	if _, ok := ReadProgress(dir); ok {
		t.Error("a record whose writer ended was read")
	}
	write(os.Getppid())
	ClearProgress(dir) // Another live process's record stays.
	if p, ok := ReadProgress(dir); !ok || p.String() != "learn: reading" {
		t.Errorf("ReadProgress = %+v, %v", p, ok)
	}
}
