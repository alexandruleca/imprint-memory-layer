package hooks

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

func TestHookCommandsExecuteWithSpacesAndApostrophes(t *testing.T) {
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	interpreter, err := exec.LookPath(python)
	if err != nil {
		t.Skip("Python needed for hook execution fixture")
	}
	root := filepath.Join(t.TempDir(), "Imprint's install with spaces")
	if err := os.MkdirAll(filepath.Join(root, "imprint"), 0755); err != nil {
		t.Fatal(err)
	}
	entry, err := os.ReadFile(filepath.Join("..", "..", "imprint", "hook_entry.py"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "imprint", "hook_entry.py"), entry, 0644); err != nil {
		t.Fatal(err)
	}
	bp := Paths{ProjectDir: root, VenvPython: interpreter, DataDir: filepath.Join(root, "user's data")}
	run := func(command string) error {
		var child *exec.Cmd
		if runtime.GOOS == "windows" {
			child = exec.Command("cmd", "/C", command)
		} else {
			child = exec.Command("sh", "-c", command)
		}
		child.Stdin = bytes.NewBufferString(`{"session_id":"fixture"}`)
		output, err := child.CombinedOutput()
		if err != nil {
			t.Logf("%s: %s", command, output)
		}
		return err
	}
	if err := run(PreReadGateCommand(bp)); err == nil {
		t.Fatal("unsearched session should be gated")
	}
	if err := run(PostSearchSentinelCommand(bp)); err != nil {
		t.Fatal(err)
	}
	if err := run(PreReadGateCommand(bp)); err != nil {
		t.Fatal(err)
	}
}
