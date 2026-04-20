//go:build !windows

package cmd

// tryInstallPythonWindows is a no-op on non-Windows platforms. The Windows
// implementation lives in setup_python_windows.go behind a build tag so the
// reg.exe / winget machinery doesn't leak into cross-platform builds.
func tryInstallPythonWindows() bool { return false }
