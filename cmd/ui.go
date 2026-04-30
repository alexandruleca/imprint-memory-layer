package cmd

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

const defaultUIPort = 8420

type uiState struct {
	PID  int `json:"pid"`
	Port int `json:"port"`
}

// UI dispatches `imprint ui` subcommands.
//
//	imprint ui                   — foreground (exits when you Ctrl+C)
//	imprint ui start [--port N]  — background daemon, detached
//	imprint ui stop              — stop the background daemon
//	imprint ui status            — show pid + URL + reachability
//	imprint ui open [--port N]   — start if stopped, then open a browser window
//	imprint ui log               — print the log file path (for `tail -f`)
func UI(args []string) {
	if len(args) > 0 {
		switch args[0] {
		case "start":
			uiStart(args[1:])
			return
		case "stop":
			uiStop(args[1:])
			return
		case "status":
			uiStatus(args[1:])
			return
		case "open":
			uiOpen(args[1:])
			return
		case "log":
			uiLog(args[1:])
			return
		case "restart":
			uiStop(nil)
			uiStart(args[1:])
			return
		}
	}
	uiForeground(args)
}

// ── Foreground (legacy behaviour) ──────────────────────────────

func uiForeground(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	pyArgs := append([]string{"-m", "imprint.api", "--auto-shutdown"}, args...)

	cmd := runner.CommandWithEnv(venvPython, pyArgs,
		"PYTHONPATH="+projectDir,
		"IMPRINT_DATA_DIR="+dataDir,
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	if err := cmd.Start(); err != nil {
		output.Fail("Failed to start server: " + err.Error())
	}

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigCh
		if cmd.Process != nil {
			_ = cmd.Process.Signal(os.Interrupt)
		}
	}()

	_ = cmd.Wait()
}

// ── Background lifecycle ───────────────────────────────────────

func uiStart(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)
	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	port := parsePort(args, defaultUIPort)
	extra := stripPort(args)

	if st, ok := readUIState(dataDir); ok && uiHealthy(st.Port) {
		output.Info(fmt.Sprintf("Already running at http://127.0.0.1:%d (pid %d)", st.Port, st.PID))
		return
	}

	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		output.Fail("cannot create data dir: " + err.Error())
	}

	logPath := uiLogFile(dataDir)
	logFH, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		output.Fail("cannot open log file: " + err.Error())
	}
	fmt.Fprintf(logFH, "\n--- imprint ui start @ %s ---\n", time.Now().Format(time.RFC3339))

	pyArgs := append(
		[]string{"-m", "imprint.api", "--port", strconv.Itoa(port), "--no-browser"},
		extra...,
	)

	cmd := runner.CommandWithEnv(venvPython, pyArgs,
		"PYTHONPATH="+projectDir,
		"IMPRINT_DATA_DIR="+dataDir,
	)
	cmd.Stdout = logFH
	cmd.Stderr = logFH
	cmd.Stdin = nil
	cmd.SysProcAttr = detachAttrs()

	if err := cmd.Start(); err != nil {
		_ = logFH.Close()
		output.Fail("failed to spawn UI server: " + err.Error())
	}

	pid := cmd.Process.Pid
	_ = logFH.Close()
	_ = cmd.Process.Release() // let it keep running once this CLI exits

	if err := writeUIState(dataDir, &uiState{PID: pid, Port: port}); err != nil {
		output.Warn("state file write failed: " + err.Error())
	}

	if waitUIReady(port, 20*time.Second) {
		output.Success(fmt.Sprintf("UI server running at http://127.0.0.1:%d (pid %d)", port, pid))
		output.Info("Log: " + logPath)
		output.Info("Open it with: imprint ui open")
	} else {
		output.Warn(fmt.Sprintf("Spawned pid %d but /api/ping didn't answer in 20s", pid))
		printLogTail(logPath, 30)
	}
}

func uiStop(_ []string) {
	projectDir := platform.FindProjectDir()
	dataDir := platform.DataDir(projectDir)

	st, ok := readUIState(dataDir)
	if !ok {
		output.Info("UI server not running (no state file)")
		return
	}

	if err := stopProcess(st.PID); err != nil {
		output.Warn(fmt.Sprintf("signal pid %d: %s", st.PID, err.Error()))
	}

	// Wait briefly for the port to clear.
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if !uiHealthy(st.Port) {
			break
		}
		time.Sleep(150 * time.Millisecond)
	}

	_ = os.Remove(uiStateFile(dataDir))
	output.Success(fmt.Sprintf("Stopped UI server (pid %d, port %d)", st.PID, st.Port))
}

func uiStatus(_ []string) {
	projectDir := platform.FindProjectDir()
	dataDir := platform.DataDir(projectDir)

	st, ok := readUIState(dataDir)
	if !ok {
		output.Info("UI server: stopped")
		return
	}
	healthy := uiHealthy(st.Port)
	if healthy {
		output.Success(fmt.Sprintf("UI server: running — http://127.0.0.1:%d (pid %d)", st.Port, st.PID))
	} else {
		output.Warn(fmt.Sprintf("UI server: stale state (pid %d, port %d did not respond)", st.PID, st.Port))
		output.Info("Run `imprint ui stop` then `imprint ui start` to recover")
	}
}

func uiOpen(args []string) {
	projectDir := platform.FindProjectDir()
	dataDir := platform.DataDir(projectDir)
	port := parsePort(args, 0)

	st, ok := readUIState(dataDir)
	needStart := !ok || !uiHealthy(st.Port) || (port != 0 && st.Port != port)

	if needStart {
		if ok && st != nil {
			_ = stopProcess(st.PID)
			_ = os.Remove(uiStateFile(dataDir))
		}
		uiStart(args)
		st, ok = readUIState(dataDir)
		if !ok {
			output.Fail("UI server failed to start; run `imprint ui log` for details")
		}
	}

	if !uiHealthy(st.Port) {
		output.Fail(fmt.Sprintf("Server state says pid %d on port %d but it isn't answering — check `imprint ui log`", st.PID, st.Port))
	}

	url := fmt.Sprintf("http://127.0.0.1:%d", st.Port)
	launchBrowser(projectDir, dataDir, url)
	output.Success("Opened " + url)
}

func uiLog(_ []string) {
	projectDir := platform.FindProjectDir()
	dataDir := platform.DataDir(projectDir)
	output.Info("tail -f " + uiLogFile(dataDir))
}

// ── Helpers ────────────────────────────────────────────────────

func uiStateFile(dataDir string) string { return filepath.Join(dataDir, "imprint_ui.json") }
func uiLogFile(dataDir string) string   { return filepath.Join(dataDir, "imprint_ui.log") }

func printLogTail(logPath string, lines int) {
	b, err := os.ReadFile(logPath)
	if err != nil {
		output.Warn("could not read log: " + err.Error())
		output.Info("Log path: " + logPath)
		return
	}
	all := strings.Split(strings.TrimRight(string(b), "\n"), "\n")
	if len(all) > lines {
		all = all[len(all)-lines:]
	}
	output.Warn("Last " + strconv.Itoa(lines) + " lines of " + logPath + ":")
	for _, l := range all {
		fmt.Println("  " + l)
	}
}

func readUIState(dataDir string) (*uiState, bool) {
	b, err := os.ReadFile(uiStateFile(dataDir))
	if err != nil {
		return nil, false
	}
	s := &uiState{}
	if err := json.Unmarshal(b, s); err != nil || s.PID == 0 {
		return nil, false
	}
	return s, true
}

func writeUIState(dataDir string, s *uiState) error {
	b, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(uiStateFile(dataDir), b, 0o644)
}

func uiHealthy(port int) bool {
	if port <= 0 {
		return false
	}
	client := &http.Client{Timeout: 800 * time.Millisecond}
	resp, err := client.Get(fmt.Sprintf("http://127.0.0.1:%d/api/ping", port))
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode >= 200 && resp.StatusCode < 300
}

func waitUIReady(port int, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if uiHealthy(port) {
			return true
		}
		time.Sleep(200 * time.Millisecond)
	}
	return false
}

func parsePort(args []string, fallback int) int {
	for i, a := range args {
		if (a == "--port" || a == "-p") && i+1 < len(args) {
			if n, err := strconv.Atoi(args[i+1]); err == nil {
				return n
			}
		}
		if strings.HasPrefix(a, "--port=") {
			if n, err := strconv.Atoi(strings.TrimPrefix(a, "--port=")); err == nil {
				return n
			}
		}
	}
	return fallback
}

func stripPort(args []string) []string {
	out := make([]string, 0, len(args))
	skip := false
	for _, a := range args {
		if skip {
			skip = false
			continue
		}
		if a == "--port" || a == "-p" {
			skip = true
			continue
		}
		if strings.HasPrefix(a, "--port=") {
			continue
		}
		out = append(out, a)
	}
	return out
}

func launchBrowser(projectDir, dataDir, url string) {
	if launchElectron(projectDir, dataDir, url) {
		return
	}
	launchChromeFallback(projectDir, dataDir, url)
}

// launchElectron tries to open the Imprint Electron app. Resolution order:
//  1. $IMPRINT_ELECTRON env var (explicit binary path)
//  2. imprint-ui / imprint-ui.exe / imprint-ui.app next to the running imprint binary
//  3. npx electron ui-electron/ inside projectDir (dev mode)
func launchElectron(projectDir, dataDir, url string) bool {
	electronArgs := []string{
		"--imprint-url=" + url,
		"--project-dir=" + projectDir,
		"--data-dir=" + dataDir,
	}

	// 1. Explicit override
	if bin := os.Getenv("IMPRINT_ELECTRON"); bin != "" {
		return spawnDetached(bin, electronArgs)
	}

	// 2. Packaged binary next to the imprint CLI
	self, err := os.Executable()
	if err == nil {
		dir := filepath.Dir(self)

		// macOS: .app bundle distributed alongside the CLI
		if runtime.GOOS == "darwin" {
			appBundle := filepath.Join(dir, "imprint-ui.app")
			if platform.FileExists(filepath.Join(appBundle, "Contents", "Info.plist")) {
				return spawnDetached("open", append([]string{"-a", appBundle, "--args"}, electronArgs...))
			}
		}

		suffix := ""
		if strings.HasSuffix(self, ".exe") {
			suffix = ".exe"
		}
		candidate := filepath.Join(dir, "imprint-ui"+suffix)
		if platform.FileExists(candidate) {
			return spawnDetached(candidate, electronArgs)
		}
	}

	// 3. Dev mode: npx electron ui-electron/
	electronDir := filepath.Join(projectDir, "ui-electron")
	if platform.FileExists(filepath.Join(electronDir, "main.js")) {
		npx := "npx"
		if runtime.GOOS == "windows" {
			npx = "npx.cmd"
		}
		args := append([]string{"electron", electronDir}, electronArgs...)
		return spawnDetached(npx, args)
	}

	return false
}

// launchChromeFallback uses the old Python _launch_browser as a last resort.
func launchChromeFallback(projectDir, dataDir, url string) {
	venv := platform.VenvPython(projectDir)
	if !platform.FileExists(venv) {
		output.Warn("venv missing; open " + url + " in your browser")
		return
	}
	snippet := fmt.Sprintf("from imprint.api import _launch_browser; _launch_browser(%q)", url)
	cmd := runner.CommandWithEnv(venv, []string{"-c", snippet},
		"PYTHONPATH="+projectDir,
		"IMPRINT_DATA_DIR="+dataDir,
	)
	cmd.Stdout = nil
	cmd.Stderr = nil
	if err := cmd.Start(); err != nil {
		output.Warn("browser launcher failed: " + err.Error())
		return
	}
	go func() { _ = cmd.Wait() }()
}

func spawnDetached(bin string, args []string) bool {
	cmd := exec.Command(bin, args...)
	cmd.Stdout = nil
	cmd.Stderr = nil
	cmd.Stdin = nil
	cmd.SysProcAttr = detachAttrs()
	if err := cmd.Start(); err != nil {
		return false
	}
	go func() { _ = cmd.Wait() }()
	return true
}
