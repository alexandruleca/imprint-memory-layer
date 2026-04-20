package platform

import (
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
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

// ── WSL2 detection + path translation ──────────────────────────

// isWSLOverride is test-only. nil means "use the real detector".
var isWSLOverride *bool

var (
	isWSLOnce   sync.Once
	isWSLCached bool
)

// IsWSL reports whether we're running inside a WSL1/WSL2 guest. Cached after
// the first call. Detection mirrors what imprint/api.py uses: a "microsoft"
// or "wsl" substring in /proc/version (case-insensitive).
func IsWSL() bool {
	if isWSLOverride != nil {
		return *isWSLOverride
	}
	isWSLOnce.Do(func() {
		if runtime.GOOS != "linux" {
			return
		}
		data, err := os.ReadFile("/proc/version")
		if err != nil {
			return
		}
		s := strings.ToLower(string(data))
		isWSLCached = strings.Contains(s, "microsoft") || strings.Contains(s, "wsl")
	})
	return isWSLCached
}

var (
	// Drive-letter paths: `C:\...` or `C:/...`
	winDriveRe = regexp.MustCompile(`^([A-Za-z]):[\\/](.*)$`)
	// WSL UNC paths pasted from Windows Explorer: `\\wsl$\Distro\rest` or
	// `\\wsl.localhost\Distro\rest`. We strip the prefix and distro name
	// since we're already inside the distro.
	wslUNCRe = regexp.MustCompile(`^\\\\wsl(?:\$|\.localhost)\\[^\\]+\\(.*)$`)
)

// TranslateWSLPath converts a Windows-style absolute path to its WSL mount
// equivalent when running inside WSL. Returns the input unchanged on non-WSL
// hosts or when the input isn't a Windows path.
//
// Examples (WSL only):
//
//	C:\Users\alex       → /mnt/c/Users/alex
//	c:/Users/alex       → /mnt/c/Users/alex
//	\\wsl$\Ubuntu\home  → /home
//	/home/alex          → /home/alex   (unchanged)
//	relative/thing      → relative/thing   (unchanged)
func TranslateWSLPath(p string) string {
	if p == "" || !IsWSL() {
		return p
	}
	if m := winDriveRe.FindStringSubmatch(p); m != nil {
		drive := strings.ToLower(m[1])
		rest := strings.ReplaceAll(m[2], `\`, "/")
		return "/mnt/" + drive + "/" + rest
	}
	if m := wslUNCRe.FindStringSubmatch(p); m != nil {
		rest := strings.ReplaceAll(m[1], `\`, "/")
		return "/" + rest
	}
	return p
}

func PythonCandidates() []PythonCandidate {
	if runtime.GOOS == "windows" {
		return []PythonCandidate{
			{Cmd: "py", ExtraArgs: []string{"-3"}},
			{Cmd: "python3.13"},
			{Cmd: "python3.12"},
			{Cmd: "python3.11"},
			{Cmd: "python3.10"},
			{Cmd: "python3"},
			{Cmd: "python"},
		}
	}

	var candidates []PythonCandidate

	// On macOS, check Homebrew versioned formula paths first.
	// `brew install python@3.X` puts the binary at /opt/homebrew/bin/python3.X
	// (Apple Silicon) or /usr/local/bin/python3.X (Intel), and also under
	// the opt prefix. Check both so we find them even when not on PATH.
	if runtime.GOOS == "darwin" {
		for _, prefix := range []string{"/opt/homebrew", "/usr/local"} {
			// Compatible versions first (highest preferred).
			for _, minor := range []string{"13", "12", "11", "10"} {
				candidates = append(candidates, PythonCandidate{
					Cmd: filepath.Join(prefix, "bin", "python3."+minor),
				})
				candidates = append(candidates, PythonCandidate{
					Cmd: filepath.Join(prefix, "opt", "python@3."+minor, "bin", "python3."+minor),
				})
			}
			// Potentially too-new versions — won't be selected, but detected
			// so the error message can tell the user what was found.
			for _, minor := range []string{"14", "15", "16"} {
				candidates = append(candidates, PythonCandidate{
					Cmd: filepath.Join(prefix, "bin", "python3."+minor),
				})
			}
		}
	}

	// Standard PATH-based candidates, highest compatible version first.
	candidates = append(candidates,
		PythonCandidate{Cmd: "python3.13"},
		PythonCandidate{Cmd: "python3.12"},
		PythonCandidate{Cmd: "python3.11"},
		PythonCandidate{Cmd: "python3.10"},
		PythonCandidate{Cmd: "python3"},
		PythonCandidate{Cmd: "python"},
	)

	return candidates
}

// IsAppBundle reports whether the given project directory is inside a macOS
// .app bundle, where the filesystem is read-only after installation.
func IsAppBundle(projectDir string) bool {
	return runtime.GOOS == "darwin" && strings.Contains(projectDir, ".app/Contents/")
}

// MutableBaseDir returns the writable base directory for mutable state
// (venv, data). When projectDir is inside a read-only .app bundle, returns
// ~/.local/share/imprint; otherwise returns projectDir itself.
func MutableBaseDir(projectDir string) string {
	if IsAppBundle(projectDir) {
		return filepath.Join(HomeDir(), ".local", "share", "imprint")
	}
	return projectDir
}

// VenvDir returns the path to the Python virtual environment directory,
// redirecting to a writable location when inside an app bundle.
func VenvDir(projectDir string) string {
	return filepath.Join(MutableBaseDir(projectDir), ".venv")
}

func VenvPython(projectDir string) string {
	return VenvBin(projectDir, "python")
}

func VenvBin(projectDir, name string) string {
	base := MutableBaseDir(projectDir)
	if runtime.GOOS == "windows" {
		return filepath.Join(base, ".venv", "Scripts", name+".exe")
	}
	return filepath.Join(base, ".venv", "bin", name)
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

// CodexConfigPath returns the user-global Codex CLI config (~/.codex/config.toml).
func CodexConfigPath() string {
	return filepath.Join(HomeDir(), ".codex", "config.toml")
}

// VSCodeUserDir returns the first existing VSCode user-profile directory:
// `Code/User` first, falling back to `Code - Insiders/User`. Empty string if
// neither is present. Used to resolve Copilot and Cline-extension paths.
func VSCodeUserDir() string {
	home := HomeDir()
	var roots []string
	switch runtime.GOOS {
	case "darwin":
		base := filepath.Join(home, "Library", "Application Support")
		roots = []string{
			filepath.Join(base, "Code", "User"),
			filepath.Join(base, "Code - Insiders", "User"),
		}
	case "windows":
		appData := os.Getenv("APPDATA")
		if appData == "" {
			appData = filepath.Join(home, "AppData", "Roaming")
		}
		roots = []string{
			filepath.Join(appData, "Code", "User"),
			filepath.Join(appData, "Code - Insiders", "User"),
		}
	default:
		base := filepath.Join(home, ".config")
		roots = []string{
			filepath.Join(base, "Code", "User"),
			filepath.Join(base, "Code - Insiders", "User"),
		}
	}
	for _, r := range roots {
		if DirExists(r) {
			return r
		}
	}
	return ""
}

// CopilotMCPPath returns the user-global VSCode MCP file (`<userDir>/mcp.json`).
// Returns empty string if no VSCode install is detected.
func CopilotMCPPath() string {
	d := VSCodeUserDir()
	if d == "" {
		return ""
	}
	return filepath.Join(d, "mcp.json")
}

// ClineExtSettingsPath returns the Cline VSCode-extension MCP settings file
// (under globalStorage for saoudrizwan.claude-dev). Empty if no VSCode install.
func ClineExtSettingsPath() string {
	d := VSCodeUserDir()
	if d == "" {
		return ""
	}
	return filepath.Join(d, "globalStorage", "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json")
}

// ClineCLISettingsPath returns the Cline standalone-CLI MCP settings file
// (~/.cline/data/settings/cline_mcp_settings.json).
func ClineCLISettingsPath() string {
	return filepath.Join(HomeDir(), ".cline", "data", "settings", "cline_mcp_settings.json")
}

// CursorHooksPath returns the user-global Cursor hooks config (~/.cursor/hooks.json).
func CursorHooksPath() string {
	return filepath.Join(CursorConfigDir(), "hooks.json")
}

// CodexHooksPath returns the user-global Codex hooks config (~/.codex/hooks.json).
func CodexHooksPath() string {
	return filepath.Join(HomeDir(), ".codex", "hooks.json")
}

// CodexAgentsPath returns the user-global Codex AGENTS.md (~/.codex/AGENTS.md).
func CodexAgentsPath() string {
	return filepath.Join(HomeDir(), ".codex", "AGENTS.md")
}

// ── Windows-side user profile (WSL-aware) ──────────────────────

var (
	winUserOnce    sync.Once
	winUserProfile string
)

// WindowsUserProfile returns the Windows ``%USERPROFILE%`` as a WSL-mount
// path (e.g. ``/mnt/c/Users/alex``) when running inside WSL. Returns the
// empty string on non-WSL hosts or when the Windows user can't be resolved.
// Cached after the first call.
//
// Resolution order:
//  1. ``cmd.exe /C echo %USERPROFILE%`` — authoritative, reads the real
//     Windows env.
//  2. Scan ``/mnt/c/Users/`` and pick the only non-system entry (falls
//     back when cmd.exe is absent from PATH, e.g. in some locked-down
//     WSL configs).
func WindowsUserProfile() string {
	if !IsWSL() {
		return ""
	}
	winUserOnce.Do(func() {
		if p := resolveFromCmdExe(); p != "" {
			winUserProfile = p
			return
		}
		winUserProfile = resolveFromUsersDir()
	})
	return winUserProfile
}

func resolveFromCmdExe() string {
	cmd := exec.Command("cmd.exe", "/C", "echo", "%USERPROFILE%")
	out, err := cmd.Output()
	if err != nil {
		return ""
	}
	raw := strings.TrimSpace(string(out))
	if raw == "" || raw == "%USERPROFILE%" {
		return ""
	}
	translated := TranslateWSLPath(raw)
	if translated == "" || translated == raw {
		// Translation didn't produce a /mnt/... path — bail.
		return ""
	}
	if _, err := os.Stat(translated); err != nil {
		return ""
	}
	return translated
}

// resolveFromUsersDir scans /mnt/c/Users for the single non-system profile
// directory. Works for typical single-user Windows installs without needing
// cmd.exe on PATH.
func resolveFromUsersDir() string {
	base := "/mnt/c/Users"
	entries, err := os.ReadDir(base)
	if err != nil {
		return ""
	}
	systemDirs := map[string]struct{}{
		"Public": {}, "Default": {}, "Default User": {}, "All Users": {},
		"WDAGUtilityAccount": {}, "desktop.ini": {},
	}
	var found string
	for _, e := range entries {
		name := e.Name()
		if strings.HasPrefix(name, ".") {
			continue
		}
		if _, skip := systemDirs[name]; skip {
			continue
		}
		if !e.IsDir() {
			continue
		}
		if found != "" {
			// Multiple profiles — ambiguous, caller should specify.
			return ""
		}
		found = filepath.Join(base, name)
	}
	return found
}

// ── Desktop app MCP config paths ───────────────────────────────

// ClaudeDesktopConfigPath returns the path where Imprint should write
// Anthropic's Claude Desktop MCP config. WSL-aware and Microsoft-Store-
// install aware.
//
// Windows (including WSL) supports two install flavours:
//
//  1. **Standalone installer** — config at ``%APPDATA%\Claude\claude_desktop_config.json``.
//  2. **Microsoft Store** (MSIX) — the app is sandboxed and the ``%APPDATA%``
//     virtualisation redirects the config under
//     ``%LOCALAPPDATA%\Packages\Claude_<publisherHash>\LocalCache\Roaming\Claude\claude_desktop_config.json``.
//     The publisher hash varies per vendor, so we glob ``Packages/Claude_*``.
//
// Resolution order:
//  1. If a Store-install config dir already exists, return that path
//     (even if the file itself hasn't been created yet).
//  2. Else return the standalone path (create-on-write).
//
// Returns the empty string if the platform is unsupported or (on WSL) the
// Windows profile can't be resolved.
func ClaudeDesktopConfigPath() string {
	candidates := claudeDesktopConfigCandidates()
	if len(candidates) == 0 {
		return ""
	}
	// Prefer a candidate whose parent directory already exists (app has run
	// at least once), so Store installs win over the standalone fallback
	// when both virtually exist.
	for _, c := range candidates {
		if DirExists(filepath.Dir(c)) {
			return c
		}
	}
	// None of the config dirs exist yet — return the first candidate so
	// the writer can create it.
	return candidates[0]
}

// claudeDesktopConfigCandidates returns every candidate config path for the
// current host, ordered by preference (Store install before standalone).
func claudeDesktopConfigCandidates() []string {
	var winProfile string
	isWin := runtime.GOOS == "windows"
	if IsWSL() {
		winProfile = WindowsUserProfile()
		if winProfile == "" {
			return nil
		}
	} else if isWin {
		winProfile = HomeDir()
	}

	if winProfile != "" {
		// Store install: %LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude
		localPackages := filepath.Join(winProfile, "AppData", "Local", "Packages")
		matches, _ := filepath.Glob(filepath.Join(localPackages, "Claude_*", "LocalCache", "Roaming", "Claude"))
		var out []string
		for _, m := range matches {
			out = append(out, filepath.Join(m, "claude_desktop_config.json"))
		}
		// Standalone: %APPDATA%\Claude (always appended as fallback).
		roamingClaude := filepath.Join(winProfile, "AppData", "Roaming", "Claude", "claude_desktop_config.json")
		out = append(out, roamingClaude)
		return out
	}

	switch runtime.GOOS {
	case "darwin":
		return []string{
			filepath.Join(HomeDir(), "Library", "Application Support", "Claude", "claude_desktop_config.json"),
		}
	case "linux":
		xdg := os.Getenv("XDG_CONFIG_HOME")
		if xdg == "" {
			xdg = filepath.Join(HomeDir(), ".config")
		}
		return []string{filepath.Join(xdg, "Claude", "claude_desktop_config.json")}
	}
	return nil
}

// ClaudeDesktopInstallMarker returns a path whose existence proves Claude
// Desktop is installed on this host. Used by SetupClaudeDesktop for the
// "is the app present?" check — the config file itself is created lazily
// (first MCP edit), so we can't use it as a presence signal.
//
// Windows/WSL: uses the same candidate list as ClaudeDesktopConfigPath
// and returns the first existing parent dir.
func ClaudeDesktopInstallMarker() string {
	if runtime.GOOS == "darwin" && !IsWSL() {
		return "/Applications/Claude.app"
	}
	for _, c := range claudeDesktopConfigCandidates() {
		if DirExists(filepath.Dir(c)) {
			return filepath.Dir(c)
		}
	}
	// Secondary Windows signals: Local/Claude (browser extension native host)
	// or a Packages/Claude_* dir even without the LocalCache/Roaming path
	// yet (e.g. app installed but never launched).
	var winProfile string
	if IsWSL() {
		winProfile = WindowsUserProfile()
	} else if runtime.GOOS == "windows" {
		winProfile = HomeDir()
	}
	if winProfile != "" {
		for _, p := range []string{
			filepath.Join(winProfile, "AppData", "Local", "Claude"),
		} {
			if DirExists(p) {
				return p
			}
		}
		matches, _ := filepath.Glob(filepath.Join(winProfile, "AppData", "Local", "Packages", "Claude_*"))
		if len(matches) > 0 {
			return matches[0]
		}
	}
	return ""
}

// ChatGPTDesktopInstallMarker returns a path that exists only when the
// OpenAI ChatGPT Desktop app is installed. WSL-aware (checks the Windows
// side of the mount). Used for detection only — ChatGPT Desktop does not
// ship a local MCP config file; MCP connectors are wired in-app via
// Settings → Connectors with a hosted (SSE) server, not a local stdio
// server like Imprint. Returns the empty string on unsupported platforms.
func ChatGPTDesktopInstallMarker() string {
	if IsWSL() {
		profile := WindowsUserProfile()
		if profile == "" {
			return ""
		}
		return filepath.Join(profile, "AppData", "Local", "Programs", "OpenAI", "ChatGPT")
	}
	switch runtime.GOOS {
	case "darwin":
		return "/Applications/ChatGPT.app"
	case "windows":
		local := os.Getenv("LOCALAPPDATA")
		if local == "" {
			local = filepath.Join(HomeDir(), "AppData", "Local")
		}
		return filepath.Join(local, "Programs", "OpenAI", "ChatGPT")
	}
	return ""
}

// OpenClawConfigDir returns the user-global OpenClaw config directory (~/.openclaw).
func OpenClawConfigDir() string {
	return filepath.Join(HomeDir(), ".openclaw")
}

// OpenClawMCPPath returns the user-global OpenClaw config file. MCP server
// registrations live under the nested `mcp.servers` key in this file.
func OpenClawMCPPath() string {
	return filepath.Join(OpenClawConfigDir(), "openclaw.json")
}

// ClineRulesPath returns the user-global Cline rules file (~/.clinerules/imprint.md).
// Cline reads every file under ~/.clinerules/ as an always-on rule.
func ClineRulesPath() string {
	return filepath.Join(HomeDir(), ".clinerules", "imprint.md")
}

// CopilotInstructionsPath returns the user-global Copilot custom-instructions
// file under VSCode's prompts dir. Returns empty if no VSCode install is
// detected.
func CopilotInstructionsPath() string {
	d := VSCodeUserDir()
	if d == "" {
		return ""
	}
	return filepath.Join(d, "prompts", "imprint.instructions.md")
}

func PalaceConfigPath() string {
	return filepath.Join(HomeDir(), ".mempalace", "config.json")
}

func PalaceDir() string {
	return filepath.Join(HomeDir(), ".mempalace")
}

// DataDir returns the data directory for imprint storage, redirecting to a
// writable location when the project dir is inside a read-only app bundle.
func DataDir(projectDir string) string {
	return filepath.Join(MutableBaseDir(projectDir), "data")
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
		return "sudo apt install python3.13 (or: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.13)"
	case "darwin":
		return "brew install python@3.13"
	case "windows":
		return "download Python 3.13 from https://www.python.org/downloads/"
	default:
		return "install Python 3.10–3.13"
	}
}

// CUDAInstallHint returns a multi-line string with distro-aware commands for
// installing the CUDA Toolkit (nvcc + headers). Blackwell GPUs (sm_120) need
// CUDA 12.8+, which is newer than most stock distro repos — the hint always
// points at NVIDIA's official repo / installer to get a current release.
func CUDAInstallHint() string {
	switch runtime.GOOS {
	case "linux":
		switch linuxDistroID() {
		case "ubuntu", "pop", "linuxmint", "neon", "elementary":
			return strings.Join([]string{
				"# Ubuntu / Debian-derivative — NVIDIA CUDA network repo",
				"wget https://developer.download.nvidia.com/compute/cuda/repos/$(. /etc/os-release; echo $ID$VERSION_ID | tr -d .)/x86_64/cuda-keyring_1.1-1_all.deb",
				"sudo dpkg -i cuda-keyring_1.1-1_all.deb",
				"sudo apt update && sudo apt install -y cuda-toolkit-12-8",
				"# then add to shell rc: export PATH=/usr/local/cuda/bin:$PATH",
			}, "\n")
		case "debian":
			return strings.Join([]string{
				"# Debian — NVIDIA CUDA network repo",
				"wget https://developer.download.nvidia.com/compute/cuda/repos/debian$(. /etc/os-release; echo $VERSION_ID)/x86_64/cuda-keyring_1.1-1_all.deb",
				"sudo dpkg -i cuda-keyring_1.1-1_all.deb",
				"sudo apt update && sudo apt install -y cuda-toolkit-12-8",
				"# then add to shell rc: export PATH=/usr/local/cuda/bin:$PATH",
			}, "\n")
		case "fedora", "rhel", "centos", "rocky", "almalinux":
			return strings.Join([]string{
				"# Fedora / RHEL-family — NVIDIA CUDA repo",
				"sudo dnf config-manager --add-repo https://developer.download.nvidia.com/compute/cuda/repos/fedora$(. /etc/os-release; echo $VERSION_ID)/x86_64/cuda-fedora$(. /etc/os-release; echo $VERSION_ID).repo",
				"sudo dnf install -y cuda-toolkit-12-8",
				"# then add to shell rc: export PATH=/usr/local/cuda/bin:$PATH",
			}, "\n")
		case "arch", "manjaro", "endeavouros":
			return "sudo pacman -S cuda   # then: export PATH=/opt/cuda/bin:$PATH"
		case "opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles":
			return strings.Join([]string{
				"# openSUSE / SLES — NVIDIA CUDA zypper repo",
				"sudo zypper ar https://developer.download.nvidia.com/compute/cuda/repos/sles15/x86_64/cuda-sles15.repo",
				"sudo zypper install -y cuda-toolkit-12-8",
				"# then add to shell rc: export PATH=/usr/local/cuda/bin:$PATH",
			}, "\n")
		default:
			return "pick installer for your distro: https://developer.nvidia.com/cuda-12-8-0-download-archive"
		}
	case "windows":
		return "download CUDA Toolkit 12.8+ installer: https://developer.nvidia.com/cuda-12-8-0-download-archive"
	case "darwin":
		return "CUDA on macOS is unsupported by NVIDIA — use a Linux or Windows host for GPU offload"
	default:
		return "see https://developer.nvidia.com/cuda-downloads"
	}
}

// linuxDistroID reads /etc/os-release and returns the ID= value (e.g.
// "ubuntu", "fedora", "arch"). Returns "" when the file is missing or
// unparseable — callers should treat that as "unknown distro".
func linuxDistroID() string {
	data, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(data), "\n") {
		if v, ok := strings.CutPrefix(line, "ID="); ok {
			return strings.ToLower(strings.Trim(v, `"`))
		}
	}
	return ""
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
