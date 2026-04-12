package runner

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

// Run executes a command and streams stdout/stderr to the terminal.
func Run(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("running %s %s: %w", name, strings.Join(args, " "), err)
	}
	return nil
}

// RunIndented executes a command and streams output indented with a prefix.
func RunIndented(prefix, name string, args ...string) error {
	cmd := exec.Command(name, args...)
	cmd.Stdout = &indentWriter{prefix: prefix, w: os.Stdout}
	cmd.Stderr = &indentWriter{prefix: prefix, w: os.Stderr}
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("running %s %s: %w", name, strings.Join(args, " "), err)
	}
	return nil
}

// RunCapture executes a command and returns its combined stdout+stderr as a string.
func RunCapture(name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	err := cmd.Run()
	return strings.TrimSpace(out.String()), err
}

// Exists checks if a command exists on the PATH.
func Exists(name string) (string, bool) {
	path, err := exec.LookPath(name)
	if err != nil {
		return "", false
	}
	return path, true
}

type indentWriter struct {
	prefix  string
	w       *os.File
	newline bool
}

func (iw *indentWriter) Write(p []byte) (int, error) {
	if !iw.newline {
		iw.newline = true
	}
	lines := bytes.Split(p, []byte("\n"))
	for i, line := range lines {
		if i == len(lines)-1 && len(line) == 0 {
			break
		}
		fmt.Fprintf(iw.w, "%s%s\n", iw.prefix, line)
	}
	return len(p), nil
}
