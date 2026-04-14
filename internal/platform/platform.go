package platform

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

type PythonCandidate struct {
	Cmd       string
	ExtraArgs []string // prepended to every invocation, e.g. ["-3"] for Windows py launcher
}

func OSName() string {
	switch runtime.GOOS {
	case "darwin":
		return "macos"
	default:
		return runtime.GOOS
	}
}

func PythonCandidates() []PythonCandidate {
	if runtime.GOOS == "windows" {
		return []PythonCandidate{
			{Cmd: "py", ExtraArgs: []string{"-3"}},
			{Cmd: "python3"},
			{Cmd: "python"},
		}
	}
	return []PythonCandidate{
		{Cmd: "python3"},
		{Cmd: "python"},
	}
}

func VenvPython(projectDir string) string {
	return VenvBin(projectDir, "python")
}

func VenvBin(projectDir, name string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(projectDir, ".venv", "Scripts", name+".exe")
	}
	return filepath.Join(projectDir, ".venv", "bin", name)
}

func HomeDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return home
}

func ClaudeSettingsPath() string {
	return filepath.Join(HomeDir(), ".claude", "settings.json")
}

// CursorConfigDir returns the user-global Cursor config directory (~/.cursor).
func CursorConfigDir() string {
	return filepath.Join(HomeDir(), ".cursor")
}

// CursorMCPPath returns the user-global Cursor MCP servers config file.
func CursorMCPPath() string {
	return filepath.Join(CursorConfigDir(), "mcp.json")
}

// CursorRulesDir returns the user-global Cursor rules directory.
// Cursor reads rules from ~/.cursor/rules/*.mdc.
func CursorRulesDir() string {
	return filepath.Join(CursorConfigDir(), "rules")
}

func PalaceConfigPath() string {
	return filepath.Join(HomeDir(), ".mempalace", "config.json")
}

func PalaceDir() string {
	return filepath.Join(HomeDir(), ".mempalace")
}

// DataDir returns the project-local data directory for palace storage.
func DataDir(projectDir string) string {
	return filepath.Join(projectDir, "data")
}

// DetectShell returns the shell name and the path to its RC file.
// On Windows, it returns "powershell" and the PowerShell profile path.
func DetectShell() (name string, rcFile string) {
	if runtime.GOOS == "windows" {
		profile := filepath.Join(HomeDir(), "Documents", "PowerShell", "Microsoft.PowerShell_profile.ps1")
		return "powershell", profile
	}

	shell := os.Getenv("SHELL")
	shellName := filepath.Base(shell)
	home := HomeDir()

	switch shellName {
	case "zsh":
		return "zsh", filepath.Join(home, ".zshrc")
	case "bash":
		if runtime.GOOS == "darwin" {
			bashrc := filepath.Join(home, ".bashrc")
			if _, err := os.Stat(bashrc); os.IsNotExist(err) {
				return "bash", filepath.Join(home, ".bash_profile")
			}
		}
		return "bash", filepath.Join(home, ".bashrc")
	case "fish":
		return "fish", filepath.Join(home, ".config", "fish", "config.fish")
	default:
		return shellName, ""
	}
}

// AliasLine returns the appropriate alias definition for the given shell and alias name.
func AliasLine(shellName, name, targetPath string) string {
	switch shellName {
	case "powershell":
		return `function ` + name + ` { & "` + targetPath + `" @args }`
	case "fish":
		return `alias ` + name + ` "` + targetPath + `"`
	default:
		return `alias ` + name + `="` + targetPath + `"`
	}
}

// AliasSearchPattern returns the string to grep for when checking if an alias already exists.
func AliasSearchPattern(shellName, name string) string {
	if shellName == "powershell" {
		return "function " + name
	}
	return "alias " + name
}

// ReplaceAliasBlock replaces an existing alias line for the given name.
// Looks for the "# <name> CLI alias" marker first (comment + next line).
// Falls back to finding the bare alias/function line directly.
func ReplaceAliasBlock(content, name, newAliasLine string) string {
	marker := "# " + name + " CLI alias"
	idx := strings.Index(content, marker)
	if idx >= 0 {
		// Marker found — replace the line after it
		afterMarker := idx + len(marker)
		if afterMarker < len(content) && content[afterMarker] == '\n' {
			afterMarker++
		}
		endOfAlias := strings.Index(content[afterMarker:], "\n")
		if endOfAlias < 0 {
			endOfAlias = len(content) - afterMarker
		}
		end := afterMarker + endOfAlias
		return content[:afterMarker] + newAliasLine + content[end:]
	}

	// No marker — find the bare alias line
	for _, prefix := range []string{
		`alias ` + name + `=`,
		`alias ` + name + ` `,
		`function ` + name,
	} {
		idx = strings.Index(content, prefix)
		if idx >= 0 {
			endOfLine := strings.Index(content[idx:], "\n")
			if endOfLine < 0 {
				endOfLine = len(content) - idx
			}
			end := idx + endOfLine
			return content[:idx] + newAliasLine + content[end:]
		}
	}

	return content
}

func PythonInstallHint() string {
	switch runtime.GOOS {
	case "linux":
		return "sudo apt install python3"
	case "darwin":
		return "brew install python3"
	case "windows":
		return "download from https://www.python.org/downloads/"
	default:
		return "install Python 3.9+"
	}
}

func PipInstallHint() string {
	switch runtime.GOOS {
	case "linux":
		return "sudo apt install python3-pip"
	case "darwin":
		return "python3 -m ensurepip --upgrade"
	case "windows":
		return "python -m ensurepip --upgrade"
	default:
		return "install pip"
	}
}

// projectRootMarkers are files whose presence indicates the imprint project root.
var projectRootMarkers = []string{"go.mod", "requirements.txt", "imprint/__main__.py"}

// FindProjectDir walks up from the binary's directory to find the project root
// (identified by having go.mod, requirements.txt, or imprint/__main__.py).
// If the walk-up fails, checks the standard install location (~/.local/share/imprint).
// Falls back to the binary's directory.
func FindProjectDir() string {
	exe, err := os.Executable()
	if err != nil {
		wd, _ := os.Getwd()
		return wd
	}
	exe, err = filepath.EvalSymlinks(exe)
	if err != nil {
		wd, _ := os.Getwd()
		return wd
	}

	dir := filepath.Dir(exe)
	// Walk up looking for project root markers.
	if found := walkUpForRoot(dir); found != "" {
		return found
	}

	// Fallback: check standard install location.
	installDir := filepath.Join(HomeDir(), ".local", "share", "imprint")
	if hasProjectMarker(installDir) {
		return installDir
	}

	return dir
}

// walkUpForRoot walks up from dir looking for a project root marker.
func walkUpForRoot(dir string) string {
	current := dir
	for {
		if hasProjectMarker(current) {
			return current
		}
		parent := filepath.Dir(current)
		if parent == current {
			break
		}
		current = parent
	}
	return ""
}

// hasProjectMarker returns true if any project root marker exists in dir.
func hasProjectMarker(dir string) bool {
	for _, marker := range projectRootMarkers {
		if _, err := os.Stat(filepath.Join(dir, marker)); err == nil {
			return true
		}
	}
	return false
}

// FileContains checks if a file exists and contains the given substring.
func FileContains(path, substr string) bool {
	data, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	return strings.Contains(string(data), substr)
}

// FileExists returns true if the path exists.
func FileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// DirExists returns true if the path exists and is a directory.
func DirExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}
