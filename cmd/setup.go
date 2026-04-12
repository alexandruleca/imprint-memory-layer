package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/hunter/knowledge/internal/jsonutil"
	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
	"github.com/hunter/knowledge/internal/runner"
)

func Setup() {
	projectDir := platform.FindProjectDir()
	venvDir := filepath.Join(projectDir, ".venv")

	// Detect OS
	output.Info("Detected platform: " + platform.OSName())

	// Step 1: Check Claude Code CLI
	output.Info("Checking for Claude Code CLI...")
	if claudePath, ok := runner.Exists("claude"); ok {
		output.Success("Claude Code CLI found: " + claudePath)
	} else {
		output.Fail("Claude Code CLI not found. Install it first: https://docs.anthropic.com/en/docs/claude-code/overview")
	}

	// Step 2: Find Python 3.9+
	output.Info("Checking for Python 3.9+...")
	pythonCmd, pythonArgs, pythonVer := findPython()
	if pythonCmd == "" {
		output.Fail("Python 3.9+ not found. Install with: " + platform.PythonInstallHint())
	}
	output.Success(fmt.Sprintf("Found Python %s (%s)", pythonVer, pythonCmd))

	// Step 3: Verify pip
	output.Info("Checking for pip...")
	pipArgs := append(pythonArgs, "-m", "pip", "--version")
	if _, err := runner.RunCapture(pythonCmd, pipArgs...); err != nil {
		output.Fail("pip not found. Install with: " + platform.PipInstallHint())
	}
	output.Success("pip available")

	// Step 4: Create virtual environment
	output.Info("Setting up virtual environment...")
	if platform.DirExists(venvDir) {
		output.Skip("Virtual environment at " + venvDir)
	} else {
		venvArgs := append(pythonArgs, "-m", "venv", venvDir)
		if err := runner.Run(pythonCmd, venvArgs...); err != nil {
			output.Fail("Failed to create virtual environment: " + err.Error())
		}
		output.Success("Created virtual environment at " + venvDir)
	}

	venvPython := platform.VenvPython(projectDir)
	venvPip := platform.VenvBin(projectDir, "pip")

	// Step 5: Install mempalace
	output.Info("Checking mempalace installation...")
	mpVer := ""
	if out, err := runner.RunCapture(venvPip, "show", "mempalace"); err == nil {
		mpVer = parsePackageVersion(out)
		output.Skip("mempalace " + mpVer + " already installed")
	} else {
		output.Info("Installing mempalace (this may take a minute)...")
		if err := runner.Run(venvPip, "install", "mempalace", "--quiet"); err != nil {
			output.Fail("Failed to install mempalace: " + err.Error())
		}
		if out, err := runner.RunCapture(venvPip, "show", "mempalace"); err == nil {
			mpVer = parsePackageVersion(out)
		}
		output.Success("Installed mempalace " + mpVer)
	}

	mempalace := platform.VenvBin(projectDir, "mempalace")

	// Step 6: Create data directory and initialize palace
	dataDir := platform.DataDir(projectDir)
	output.Info("Checking palace initialization...")
	projectConfig := filepath.Join(projectDir, "mempalace.yaml")
	if platform.DirExists(dataDir) && platform.FileExists(projectConfig) {
		output.Skip("Palace already initialized at " + dataDir)
	} else {
		if !platform.DirExists(dataDir) {
			os.MkdirAll(dataDir, 0755)
		}
		output.Info("Initializing palace (scanning " + projectDir + ")...")
		if err := runner.Run(mempalace, "init", projectDir, "--yes"); err != nil {
			output.Warn("Init reported issues (this is OK for an empty directory)")
		}
		output.Success("Palace initialized at " + dataDir)
	}

	// Step 7: Register MCP server with --palace pointing to data/
	output.Info("Checking MCP server registration...")
	if mcpOut, err := runner.RunCapture("claude", "mcp", "list"); err == nil && strings.Contains(mcpOut, "mempalace") {
		output.Skip("MCP server 'mempalace' already registered")
	} else {
		output.Info("Registering MCP server with Claude Code (user scope)...")
		if err := runner.Run("claude", "mcp", "add", "--scope", "user", "mempalace", "--", venvPython, "-m", "mempalace.mcp_server", "--palace", dataDir); err != nil {
			output.Fail("Failed to register MCP server: " + err.Error())
		}
		output.Success("MCP server registered globally")
	}

	// Step 8: Add permissions
	output.Info("Checking Claude Code permissions...")
	settingsPath := platform.ClaudeSettingsPath()
	added, err := jsonutil.EnsurePermission(settingsPath, "mcp__mempalace__*")
	if err != nil {
		output.Warn("Could not update " + settingsPath + ": " + err.Error())
	} else if added {
		output.Success("Added mempalace permissions to " + settingsPath)
	} else {
		output.Skip("mempalace permissions already configured")
	}

	// Step 9: Claude Code hooks
	output.Info("Checking Claude Code hooks...")
	setupHooks(settingsPath, mempalace, dataDir)

	// Step 10: Global CLAUDE.md
	output.Info("Checking global CLAUDE.md...")
	setupGlobalClaudeMD()

	// Step 11: Shell aliases (knowledge + mempalace)
	output.Info("Setting up shell aliases...")
	knowledgeBin, _ := os.Executable()
	knowledgeBin, _ = filepath.EvalSymlinks(knowledgeBin)
	knowledgeBin, _ = filepath.Abs(knowledgeBin)
	setupShellAlias("knowledge", knowledgeBin)
	setupShellAlias("mempalace", mempalace)

	// Summary
	output.Header("═══ MemPalace Setup Complete ═══")
	venvPythonVer, _ := runner.RunCapture(venvPython, "--version")
	fmt.Printf("  Python:      %s (%s)\n", venvPythonVer, venvPython)
	fmt.Printf("  Venv:        %s\n", venvDir)
	fmt.Printf("  mempalace:   %s\n", mpVer)
	fmt.Printf("  Palace:      %s\n", dataDir)
	fmt.Printf("  MCP server:  registered (user scope)\n")
	fmt.Println()
	output.Info("Next steps:")
	fmt.Println("  1. Restart Claude Code to load the MCP server")
	fmt.Println("  2. Run /mcp in a session to verify mempalace tools are available")
	fmt.Println("  3. Use 'mempalace init <project-dir>' to index your project directories")
	fmt.Println("  4. Use 'mempalace mine <dir>' to ingest project files into the palace")
}

var pythonVersionRe = regexp.MustCompile(`Python (\d+)\.(\d+)\.(\d+)`)

func findPython() (cmd string, extraArgs []string, version string) {
	for _, candidate := range platform.PythonCandidates() {
		args := append(candidate.ExtraArgs, "--version")
		out, err := runner.RunCapture(candidate.Cmd, args...)
		if err != nil {
			continue
		}
		matches := pythonVersionRe.FindStringSubmatch(out)
		if matches == nil {
			continue
		}
		major, _ := strconv.Atoi(matches[1])
		minor, _ := strconv.Atoi(matches[2])
		if major > 3 || (major == 3 && minor >= 9) {
			return candidate.Cmd, candidate.ExtraArgs, matches[1] + "." + matches[2] + "." + matches[3]
		}
	}
	return "", nil, ""
}

func parsePackageVersion(pipShowOutput string) string {
	for _, line := range strings.Split(pipShowOutput, "\n") {
		if strings.HasPrefix(line, "Version:") {
			return strings.TrimSpace(strings.TrimPrefix(line, "Version:"))
		}
	}
	return "unknown"
}

func setupShellAlias(name, targetPath string) {
	shellName, rcFile := platform.DetectShell()
	if rcFile == "" {
		output.Warn("Unknown shell '" + shellName + "' — add alias manually: alias " + name + "=\"" + targetPath + "\"")
		return
	}

	searchPattern := platform.AliasSearchPattern(shellName, name)
	if platform.FileContains(rcFile, searchPattern) {
		output.Skip(name + " alias in " + rcFile)
		return
	}

	aliasLine := platform.AliasLine(shellName, name, targetPath)

	// Create parent directory if needed (e.g., fish config dir)
	dir := filepath.Dir(rcFile)
	if !platform.DirExists(dir) {
		os.MkdirAll(dir, 0755)
	}

	f, err := os.OpenFile(rcFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		output.Warn("Could not write to " + rcFile + ": " + err.Error())
		return
	}
	defer f.Close()

	entry := "\n# " + name + " CLI alias\n" + aliasLine + "\n"
	if _, err := f.WriteString(entry); err != nil {
		output.Warn("Could not write alias to " + rcFile + ": " + err.Error())
		return
	}
	output.Success("Added " + name + " alias to " + rcFile)
}

func setupHooks(settingsPath, mempalace, dataDir string) {
	// The hook commands pipe Claude Code's stdin JSON through to mempalace's hook runner
	baseCmd := mempalace + " --palace " + dataDir + " hook run --harness claude-code"

	type hookDef struct {
		event   string
		hook    string
		timeout int
	}

	hooks := []hookDef{
		{"Stop", "stop", 60},
		{"PreCompact", "precompact", 90},
	}

	anyAdded := false
	for _, h := range hooks {
		cmd := baseCmd + " --hook " + h.hook
		added, err := jsonutil.EnsureHook(settingsPath, h.event, cmd, h.timeout)
		if err != nil {
			output.Warn("Could not add " + h.event + " hook: " + err.Error())
			continue
		}
		if added {
			output.Success("Added " + h.event + " hook")
			anyAdded = true
		}
	}

	if !anyAdded {
		output.Skip("Claude Code hooks already configured")
	}
}

const globalClaudeMD = `# Global Instructions

## MemPalace — Use Memory Before Reading Files

A MemPalace MCP server is registered globally. It stores project knowledge (decisions, patterns, architecture, bug fixes, user preferences) across sessions.

### Every conversation:
1. Call mcp__mempalace__wake_up at the start to load prior context (~170 tokens)
2. Before reading files for context or background, call mcp__mempalace__search with a relevant query first
3. Only fall back to Read/Grep when the palace has no results or you need current file content (live edits, specific line numbers)
4. Store important discoveries, decisions, and patterns via MemPalace tools during the conversation

### What to store:
- Architectural decisions and why they were made
- Bug root causes and how they were fixed
- Project conventions and patterns
- User corrections and preferences
- Cross-project relationships

### What NOT to store:
- Current file contents (they change — read them live)
- Temporary debugging state
- Things already in git history

This saves tokens and API usage by not re-reading files and re-discovering context from previous sessions.
`

func setupGlobalClaudeMD() {
	claudeDir := filepath.Join(platform.HomeDir(), ".claude")
	claudeMD := filepath.Join(claudeDir, "CLAUDE.md")

	if platform.FileExists(claudeMD) && platform.FileContains(claudeMD, "MemPalace") {
		output.Skip("Global CLAUDE.md already has MemPalace instructions")
		return
	}

	if !platform.DirExists(claudeDir) {
		os.MkdirAll(claudeDir, 0755)
	}

	if platform.FileExists(claudeMD) {
		// Append to existing file
		f, err := os.OpenFile(claudeMD, os.O_APPEND|os.O_WRONLY, 0644)
		if err != nil {
			output.Warn("Could not write to " + claudeMD + ": " + err.Error())
			return
		}
		defer f.Close()
		if _, err := f.WriteString("\n" + globalClaudeMD); err != nil {
			output.Warn("Could not append to " + claudeMD + ": " + err.Error())
			return
		}
		output.Success("Appended MemPalace instructions to " + claudeMD)
	} else {
		// Create new file
		if err := os.WriteFile(claudeMD, []byte(globalClaudeMD), 0644); err != nil {
			output.Warn("Could not create " + claudeMD + ": " + err.Error())
			return
		}
		output.Success("Created " + claudeMD + " with MemPalace instructions")
	}
}
