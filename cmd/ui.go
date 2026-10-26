package cmd

import (
	"encoding/json"
	"errors"
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
	"github.com/hunter/imprint/internal/queuelock"
	"github.com/hunter/imprint/internal/runner"
)

const defaultUIPort = 8420

type uiState struct {
	PID  int `json:"pid"`
	Port int `json:"port"`
}

// uiRestartExitCode is the status a dashboard server exits with when asked to
// restart. Whoever started it starts it again (ADR 0050). EX_TEMPFAIL.
const uiRestartExitCode = 75

// errNoUIServer reports that no dashboard server is running.
var errNoUIServer = errors.New("UI server not running")

// UI dispatches `imprint ui` subcommands. Every dashboard server registers
// itself in data/imprint_ui.json while it serves, whichever launcher started
// it (the desktop app or this command), so stop, status and restart act on
// the server that is actually running (ADR 0050).
//
//	imprint ui                   — foreground (exits when you Ctrl+C)
//	imprint ui start [--port N]  — background daemon, detached
//	imprint ui stop              — stop the running server
//	imprint ui status            — show pid + URL + reachability
//	imprint ui restart           — restart the running server with the installed code
//	imprint ui open [--port N]   — open the desktop app (browser fallback)
//	imprint ui runtime           — print the installation the desktop app serves, as JSON
//	imprint ui log               — print the log file path (for `tail -f`)
//	imprint ui serve [--port N]  — what `ui start` runs detached: the server, restarted on request
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
		case "runtime":
			uiRuntime(args[1:])
			return
		case "log":
			uiLog(args[1:])
			return
		case "restart":
			uiRestart(args[1:])
			return
		case "serve":
			uiServe(args[1:])
			return
		}
	}
	uiForeground(args)
}

// ── Foreground (legacy behaviour) ──────────────────────────────

func uiForeground(args []string) {
	superviseUI(append([]string{"-m", "imprint.api", "--auto-shutdown"}, args...))
}

// uiServe is the process `imprint ui start` leaves running: the server in the
// foreground of a detached process whose output is the UI log.
func uiServe(args []string) {
	superviseUI(append([]string{"-m", "imprint.api", "--no-browser"}, args...))
}

// superviseUI runs the dashboard server with this process's standard streams
// and starts it again each time it exits for a restart, so a restart serves
// the installed code under the same owner (ADR 0050). Each start waits while
// setup changes the runtime (ADR 0084).
func superviseUI(pyArgs []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)
	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)
	err := superviseServer(func() *exec.Cmd {
		if err := queuelock.WaitRuntime(dataDir); err != nil {
			output.Warn("Could not check for a running setup: " + err.Error())
		}
		cmd := runner.CommandWithEnv(venvPython, pyArgs,
			"PYTHONPATH="+projectDir,
			"IMPRINT_DATA_DIR="+dataDir,
		)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		cmd.Stdin = os.Stdin
		return cmd
	}, signals)
	var exited *exec.ExitError
	if err != nil && !errors.As(err, &exited) {
		output.Fail("Failed to start server: " + err.Error())
	}
}

// superviseServer runs the command newCmd builds until it exits for any
// reason other than a restart request. A signal is passed on to the server
// and ends supervision once it exits.
func superviseServer(newCmd func() *exec.Cmd, signals <-chan os.Signal) error {
	for {
		cmd := newCmd()
		if err := cmd.Start(); err != nil {
			return err
		}
		exited := make(chan error, 1)
		go func() { exited <- cmd.Wait() }()
		select {
		case err := <-exited:
			if exitCode(err) != uiRestartExitCode {
				return err
			}
		case <-signals:
			// Windows cannot interrupt another process; there the server
			// shares this console and receives the same console event.
			_ = cmd.Process.Signal(os.Interrupt)
			return <-exited
		}
	}
}

// exitCode is the status a command exited with: -1 when a signal ended it or
// it did not run.
func exitCode(err error) int {
	var exited *exec.ExitError
	switch {
	case err == nil:
		return 0
	case errors.As(err, &exited):
		return exited.ExitCode()
	}
	return -1
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
	self, err := os.Executable()
	if err != nil {
		output.Fail("cannot locate the imprint binary: " + err.Error())
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

	// `imprint ui serve` supervises the server, so a restart request
	// starts it again rather than stopping it.
	cmd := exec.Command(self, append([]string{"ui", "serve", "--port", strconv.Itoa(port)}, extra...)...)
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

	if waitUIReady(port, 20*time.Second) {
		if st, ok := readUIState(dataDir); ok {
			pid = st.PID
		}
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
		output.Info("UI server not running")
		return
	}

	// When the desktop app started this server, the app reports that it stopped.
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

	// A graceful exit removes the registration; a forced one leaves it.
	_ = os.Remove(uiStateFile(dataDir))
	output.Success(fmt.Sprintf("Stopped UI server (pid %d, port %d)", st.PID, st.Port))
}

// uiRestart asks the running dashboard server to restart. It exits for a
// restart and whoever started it (the desktop app or `imprint ui`) starts it
// again with the installed code. Nothing is started when no server runs.
func uiRestart(_ []string) {
	dataDir := platform.DataDir(platform.FindProjectDir())
	st, err := restartRegisteredUI(dataDir, 60*time.Second)
	switch {
	case errors.Is(err, errNoUIServer):
		output.Info("UI server not running — nothing to restart")
	case err != nil:
		output.Fail(err.Error())
	default:
		output.Success(fmt.Sprintf("Restarted UI server at http://127.0.0.1:%d (pid %d)", st.Port, st.PID))
	}
}

// restartRegisteredUI restarts the registered server and waits up to timeout
// for its replacement to answer.
func restartRegisteredUI(dataDir string, timeout time.Duration) (*uiState, error) {
	old, err := requestUIRestart(dataDir)
	if err != nil {
		return nil, err
	}
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if st, ok := readUIState(dataDir); ok && st.PID != old.PID && uiHealthy(st.Port) {
			return st, nil
		}
		time.Sleep(200 * time.Millisecond)
	}
	return nil, fmt.Errorf("the UI server (pid %d) stopped and did not start again within %s; reopen Imprint or run `imprint ui start` (`imprint ui log` has details)", old.PID, timeout)
}

// requestUIRestart asks the registered server to exit for a restart and
// returns the server it asked.
func requestUIRestart(dataDir string) (*uiState, error) {
	old, ok := readUIState(dataDir)
	if !ok || !uiHealthy(old.Port) {
		return nil, errNoUIServer
	}
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Post(fmt.Sprintf("http://127.0.0.1:%d/api/restart", old.Port), "application/json", nil)
	if err != nil {
		return nil, fmt.Errorf("asking the UI server to restart: %w", err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("the UI server (pid %d) cannot restart: HTTP %d", old.PID, resp.StatusCode)
	}
	return old, nil
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

// desktopRuntime is the installation the desktop app serves. `ui open` passes
// it to the app as arguments; `ui runtime` prints it for an app the OS
// launched without them (Dock, Reopen after a crash, a pinned taskbar icon).
type desktopRuntime struct {
	ProjectDir string `json:"project_dir"`
	DataDir    string `json:"data_dir"`
	Python     string `json:"python"`
}

func resolveDesktopRuntime() desktopRuntime {
	projectDir := platform.FindProjectDir()
	// The writable venv is outside the source bundle on installed macOS.
	return desktopRuntime{projectDir, platform.DataDir(projectDir), platform.VenvPython(projectDir)}
}

func uiRuntime(_ []string) {
	if err := json.NewEncoder(os.Stdout).Encode(resolveDesktopRuntime()); err != nil {
		output.Fail("cannot write the runtime: " + err.Error())
	}
}

func uiOpen(args []string) {
	rt := resolveDesktopRuntime()
	projectDir, dataDir := rt.ProjectDir, rt.DataDir
	port := parsePort(args, 0)

	st, ok := readUIState(dataDir)
	// Open the shell before waiting for Python. Electron owns its service and
	// displays startup progress; an explicitly started daemon stays independent.
	if port == 0 {
		port = defaultUIPort
		if ok && uiHealthy(st.Port) {
			port = st.Port
		}
	}
	url := fmt.Sprintf("http://127.0.0.1:%d", port)
	if launchElectron(rt, url) {
		output.Success("Opened " + url)
		return
	}

	// Browser-only distributions retain the explicit background-daemon model.
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

	url = fmt.Sprintf("http://127.0.0.1:%d", st.Port)
	launchChromeFallback(projectDir, dataDir, url)
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

// launchElectron tries to open the Imprint Electron app. Resolution order:
//  1. $IMPRINT_ELECTRON env var (explicit binary path)
//  2. imprint-ui / imprint-ui.exe / imprint-ui.app next to the running imprint binary
//  3. npx electron ui-electron/ inside the project directory (dev mode)
func launchElectron(rt desktopRuntime, url string) bool {
	electronArgs := []string{
		"--imprint-url=" + url,
		"--project-dir=" + rt.ProjectDir,
		"--data-dir=" + rt.DataDir,
		"--python=" + rt.Python,
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
	electronDir := filepath.Join(rt.ProjectDir, "ui-electron")
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
