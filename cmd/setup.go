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
	dataDir := platform.DataDir(projectDir)
	requirementsFile := filepath.Join(projectDir, "requirements.txt")

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

	// Step 5: Install dependencies from requirements.txt
	output.Info("Checking dependencies...")
	if out, err := runner.RunCapture(venvPip, "show", "fastmcp"); err == nil {
		ver := parsePackageVersion(out)
		output.Skip("Dependencies installed (fastmcp " + ver + ")")
	} else {
		output.Info("Installing dependencies (this may take a minute)...")
		if err := runner.Run(venvPip, "install", "-r", requirementsFile, "--quiet"); err != nil {
			output.Fail("Failed to install dependencies: " + err.Error())
		}
		output.Success("Dependencies installed")
	}

	// Step 6: Create data directory
	output.Info("Checking data directory...")
	if platform.DirExists(dataDir) {
		output.Skip("Data directory at " + dataDir)
	} else {
		os.MkdirAll(dataDir, 0755)
		output.Success("Created data directory at " + dataDir)
	}

	// Step 7: Register MCP server
	output.Info("Checking MCP server registration...")
	if mcpOut, err := runner.RunCapture("claude", "mcp", "list"); err == nil && strings.Contains(mcpOut, "knowledge") {
		output.Skip("MCP server 'knowledge' already registered")
	} else {
		// Remove old mempalace MCP if present
		if mcpOut, err := runner.RunCapture("claude", "mcp", "list"); err == nil && strings.Contains(mcpOut, "mempalace") {
			runner.RunCapture("claude", "mcp", "remove", "mempalace")
		}
		output.Info("Registering MCP server with Claude Code (user scope)...")
		if err := runner.Run("claude", "mcp", "add", "--scope", "user",
			"knowledge",
			"-e", "PYTHONPATH="+projectDir,
			"--", venvPython, "-m", "knowledgebase"); err != nil {
			output.Fail("Failed to register MCP server: " + err.Error())
		}
		output.Success("MCP server registered globally")
	}

	// Step 8: Add permissions
	output.Info("Checking Claude Code permissions...")
	settingsPath := platform.ClaudeSettingsPath()
	added, err := jsonutil.EnsurePermission(settingsPath, "mcp__knowledge__*")
	if err != nil {
		output.Warn("Could not update " + settingsPath + ": " + err.Error())
	} else if added {
		output.Success("Added knowledge permissions to " + settingsPath)
	} else {
		output.Skip("knowledge permissions already configured")
	}

	// Step 9: Claude Code hooks
	output.Info("Checking Claude Code hooks...")
	setupHooks(settingsPath)

	// Step 10: Global CLAUDE.md
	output.Info("Checking global CLAUDE.md...")
	setupGlobalClaudeMD()

	// Step 11: Shell aliases
	output.Info("Setting up shell aliases...")
	knowledgeBin, _ := os.Executable()
	knowledgeBin, _ = filepath.EvalSymlinks(knowledgeBin)
	knowledgeBin, _ = filepath.Abs(knowledgeBin)
	setupShellAlias("knowledge", knowledgeBin)

	// Summary
	output.Header("═══ Knowledge Setup Complete ═══")
	venvPythonVer, _ := runner.RunCapture(venvPython, "--version")
	fmt.Printf("  Python:      %s (%s)\n", venvPythonVer, venvPython)
	fmt.Printf("  Venv:        %s\n", venvDir)
	fmt.Printf("  Data:        %s\n", dataDir)
	fmt.Printf("  MCP server:  knowledge (user scope)\n")
	fmt.Println()
	output.Info("Next steps:")
	fmt.Println("  1. Restart Claude Code to load the MCP server")
	fmt.Println("  2. Run /mcp in a session to verify knowledge tools are available")
	fmt.Println("  3. Use 'knowledge index <dir>' to index your project directories")
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

func setupHooks(settingsPath string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	type hookDef struct {
		event   string
		command string
		timeout int
		async   bool
	}

	// Stop hook: index conversation exchanges + extract decisions (async, background)
	// Reads transcript_path from stdin JSON, indexes exchanges, then extracts decisions
	stopCmd := fmt.Sprintf(
		`PYTHONPATH=%s KNOWLEDGE_DATA_DIR=%s %s -c "
import json,sys,subprocess,os
d=json.loads(sys.stdin.read())
tp=d.get('transcript_path','')
if tp:
    subprocess.run([sys.executable,'-m','knowledgebase.cli_conversations','--transcript',tp],env=os.environ)
    subprocess.run([sys.executable,'-m','knowledgebase.cli_extract',tp],env=os.environ)
" 2>/dev/null`,
		projectDir, dataDir, venvPython,
	)

	hooks := []hookDef{
		// Stop: index conversation + extract decisions
		{"Stop", stopCmd, 120, true},
		// PreCompact: instruct Claude to save everything before context compression
		{"PreCompact", "echo '{\"decision\":\"block\",\"reason\":\"COMPACTION IMMINENT. Save ALL topics, decisions, and important context from this session using the knowledge MCP tools (store, kg_add). Be thorough — after compaction, detailed context will be lost.\"}'", 90, false},
	}

	for _, h := range hooks {
		if err := jsonutil.SetHook(settingsPath, h.event, h.command, h.timeout, h.async); err != nil {
			output.Warn("Could not set " + h.event + " hook: " + err.Error())
		} else {
			output.Success("Configured " + h.event + " hook")
		}
	}
}

const globalClaudeMD = `# Global Instructions

## Knowledge Base — Check Memory First

A Knowledge MCP server is registered globally. It contains indexed code chunks, decisions, patterns, and project knowledge from past sessions.

### Every conversation:
1. Call mcp__knowledge__wake_up at the start to load prior context
2. Before answering questions about code, architecture, or project context — call mcp__knowledge__search first
3. If search returns relevant results, use them to answer. The knowledge base contains actual code chunks — often enough for explanations without reading files
4. If the context from search isn't enough, or you need exact current file content for edits, read the files as needed

### During conversation — store what you learn:
- Architectural decisions and WHY they were made
- Bug root causes and how they were fixed
- Project conventions and patterns
- User corrections and preferences

### Do NOT store:
- Raw file contents (already indexed)
- Temporary debugging state
- Things derivable from git history
`

func setupGlobalClaudeMD() {
	claudeDir := filepath.Join(platform.HomeDir(), ".claude")
	claudeMD := filepath.Join(claudeDir, "CLAUDE.md")

	if platform.FileExists(claudeMD) && platform.FileContains(claudeMD, "Knowledge") && platform.FileContains(claudeMD, "mcp__knowledge") {
		output.Skip("Global CLAUDE.md already has Knowledge instructions")
		return
	}

	if !platform.DirExists(claudeDir) {
		os.MkdirAll(claudeDir, 0755)
	}

	// Always overwrite — we own this file
	if err := os.WriteFile(claudeMD, []byte(globalClaudeMD), 0644); err != nil {
		output.Warn("Could not write " + claudeMD + ": " + err.Error())
		return
	}
	output.Success("Updated " + claudeMD)
}
