//go:build windows

package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

// The test binary plays both parts: a detached console program that starts a
// console child without flags, as `imprint ui serve` starts Python, and that
// child, which reports whether it has a visible console window.
func init() {
	switch os.Getenv("IMPRINT_TEST_CONSOLE") {
	case "parent":
		child := exec.Command(os.Args[0], "-test.run=^$")
		child.Env = append(os.Environ(), "IMPRINT_TEST_CONSOLE=child")
		child.Stdout = os.Stdout
		if err := child.Run(); err != nil {
			os.Exit(1)
		}
		os.Exit(0)
	case "child":
		window, _, _ := syscall.NewLazyDLL("kernel32.dll").NewProc("GetConsoleWindow").Call()
		visible := uintptr(0)
		if window != 0 {
			visible, _, _ = syscall.NewLazyDLL("user32.dll").NewProc("IsWindowVisible").Call(window)
		}
		fmt.Printf("visible=%t\n", visible != 0)
		os.Exit(0)
	}
}

func TestDetachedConsoleProgramsStartConsoleChildrenWithoutAWindow(t *testing.T) {
	path := filepath.Join(t.TempDir(), "console.txt")
	out, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	parent := exec.Command(os.Args[0], "-test.run=^$")
	parent.Env = append(os.Environ(), "IMPRINT_TEST_CONSOLE=parent")
	parent.Stdout = out
	parent.SysProcAttr = detachAttrs()
	err = parent.Run()
	out.Close()
	if err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(path)
	if got := strings.TrimSpace(string(b)); got != "visible=false" {
		t.Fatalf("a console child of a detached program must not show a window, got %q", got)
	}
}
