//go:build windows

package cmd

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/hunter/imprint/internal/output"
)

// Version to install when bootstrapping Python on Windows. Kept in sync with
// pythonMaxMinor in setup.go — whatever the user's imprint build supports as
// its newest tested runtime is a safe default.
const windowsPythonInstallVersion = "3.13.1"

// tryInstallPythonWindows attempts a non-interactive Python install on
// Windows. Returns true if it believes Python is now available; the caller
// should re-run findPython() to confirm.
//
// Strategy:
//  1. winget (shipped on Win10 1809+ / Win11) — scope=user, no UAC prompt.
//  2. Fall back to downloading the official python.org installer and running
//     it with /quiet InstallAllUsers=0 PrependPath=1 — also no UAC.
//
// Any error is surfaced as a warning but the overall setup continues so the
// caller can still print the "install Python manually" fallback message if
// both auto-install paths fail.
func tryInstallPythonWindows() bool {
	output.Info("No compatible Python found — attempting automatic install...")

	if _, err := exec.LookPath("winget"); err == nil {
		if installPythonViaWinget() {
			refreshWindowsPathInProcess()
			return true
		}
		output.Warn("winget install did not succeed; falling back to python.org installer.")
	} else {
		output.Info("winget is not available; downloading the python.org installer...")
	}

	if installPythonViaInstaller() {
		refreshWindowsPathInProcess()
		return true
	}
	return false
}

func installPythonViaWinget() bool {
	output.Info("Running: winget install --id Python.Python.3.13 --scope user --silent (one-time, ~40 MB)...")
	cmd := exec.Command(
		"winget", "install",
		"--id", "Python.Python.3.13",
		"--exact",
		"--silent",
		"--scope", "user",
		"--accept-source-agreements",
		"--accept-package-agreements",
		"--disable-interactivity",
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	err := cmd.Run()
	if err != nil {
		output.Warn("winget install failed: " + err.Error())
		return false
	}
	output.Success("winget installed Python 3.13 into the user scope.")
	return true
}

func installPythonViaInstaller() bool {
	url := fmt.Sprintf("https://www.python.org/ftp/python/%s/python-%s-amd64.exe",
		windowsPythonInstallVersion, windowsPythonInstallVersion)

	tmp, err := os.MkdirTemp("", "imprint-python-install-*")
	if err != nil {
		output.Warn("Could not create temp dir for Python installer: " + err.Error())
		return false
	}
	defer os.RemoveAll(tmp)

	installerPath := filepath.Join(tmp, "python-installer.exe")

	output.Info("Downloading " + url + " (~27 MB)...")
	if err := downloadFile(url, installerPath); err != nil {
		output.Warn("Python installer download failed: " + err.Error())
		return false
	}

	// Silent per-user install — no admin prompt, adds python.exe to PATH.
	// Include launcher so `py -3` also works. Skip docs/tests to trim size.
	args := []string{
		"/quiet",
		"InstallAllUsers=0",
		"PrependPath=1",
		"Include_launcher=1",
		"Include_test=0",
		"Include_doc=0",
		"SimpleInstall=1",
	}
	output.Info("Running Python installer silently (no UAC prompt)...")
	cmd := exec.Command(installerPath, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		output.Warn("python.org installer failed: " + err.Error())
		return false
	}
	output.Success(fmt.Sprintf("Installed Python %s into the user scope.", windowsPythonInstallVersion))
	return true
}

// downloadFile streams a URL to disk with a generous timeout.
func downloadFile(url, dst string) error {
	client := &http.Client{Timeout: 5 * time.Minute}
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "imprint-setup")
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("HTTP %d for %s", resp.StatusCode, url)
	}
	f, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(f, resp.Body)
	return err
}

// refreshWindowsPathInProcess re-reads HKCU\Environment and HKLM PATH so the
// newly-installed Python shows up in findPython() without requiring the user
// to restart their shell. PATH changes via the registry don't propagate to
// already-running processes, which is why both winget and the MSI installer
// normally require a new shell.
func refreshWindowsPathInProcess() {
	user, _ := readRegistryString(`HKCU\Environment`, "Path")
	machine, _ := readRegistryString(`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`, "Path")
	combined := strings.Trim(strings.Join([]string{machine, user}, string(os.PathListSeparator)), string(os.PathListSeparator))
	if combined == "" {
		return
	}
	// Expand %SystemRoot% etc.
	combined = os.ExpandEnv(combined)
	if err := os.Setenv("PATH", combined); err != nil {
		output.Warn("could not refresh PATH in-process: " + err.Error())
	}
}

// readRegistryString shells out to reg.exe — stdlib would require syscall/registry
// imports that aren't worth the build-tag gymnastics for a one-off lookup.
func readRegistryString(key, name string) (string, error) {
	out, err := exec.Command("reg", "query", key, "/v", name).Output()
	if err != nil {
		return "", err
	}
	// reg query output: "    Path    REG_EXPAND_SZ    C:\..."
	for _, line := range strings.Split(string(out), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 3 && strings.EqualFold(fields[0], name) {
			return strings.Join(fields[2:], " "), nil
		}
	}
	return "", fmt.Errorf("reg value %s not found under %s", name, key)
}
