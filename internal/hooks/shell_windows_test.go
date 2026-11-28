package hooks

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

// copilotShellCommand runs a command the way Copilot runs a hook on Windows.
func copilotShellCommand(command string) *exec.Cmd {
	return exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command)
}

func hookShellCommand(command string) *exec.Cmd {
	// cmd.exe parses its /c tail itself. Go's default CRT argument escaping
	// inserts literal backslashes before these quotes; use cmd's raw syntax.
	child := exec.Command("cmd.exe")
	child.SysProcAttr = &syscall.SysProcAttr{CmdLine: `cmd.exe /d /s /c "` + command + `"`}
	return child
}

// cursorShellCommand runs a hook the way Cursor does on Windows (ADR 0236):
// the payload is written to a file, read back with Get-Content and piped into
// the command inside a script block, with UTF-8 as PowerShell's output
// encoding, which puts a byte order mark before it.
func cursorShellCommand(t *testing.T, command, payload string) *exec.Cmd {
	file := filepath.Join(t.TempDir(), "payload.json")
	if err := os.WriteFile(file, []byte(payload), 0644); err != nil {
		t.Fatal(err)
	}
	script := "$OutputEncoding = [System.Text.Encoding]::UTF8; Get-Content -LiteralPath '" +
		strings.ReplaceAll(file, "'", "''") + "' -Raw | & { $input | " + command + " }"
	child := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script)
	// Cursor starts the shell from Node. A test run from PowerShell 7 would
	// hand Windows PowerShell 7's module path, which can keep it from
	// loading its own modules (Get-Content among them).
	for _, entry := range os.Environ() {
		if !strings.HasPrefix(strings.ToUpper(entry), "PSMODULEPATH=") {
			child.Env = append(child.Env, entry)
		}
	}
	return child
}
