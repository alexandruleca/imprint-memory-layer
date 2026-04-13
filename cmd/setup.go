package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/hunter/knowledge/internal/instructions"
	"github.com/hunter/knowledge/internal/jsonutil"
	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
	"github.com/hunter/knowledge/internal/runner"
)

// backendPaths holds the resolved local paths produced by setupBackend.
// Every target (Claude Code, Cursor, ...) reuses these so the venv, data
// directory, and shell alias are installed once per machine.
type backendPaths struct {
	ProjectDir string
	VenvPython string
	DataDir    string
}

// setupBackend installs everything that is independent of the host AI tool:
// Python venv, dependencies, data directory, shell alias. Returns the
// resolved paths so target-specific code can wire them into MCP configs.
func setupBackend() backendPaths {
	projectDir := platform.FindProjectDir()
	venvDir := filepath.Join(projectDir, ".venv")
	dataDir := platform.DataDir(projectDir)
	requirementsFile := filepath.Join(projectDir, "requirements.txt")

	output.Info("Detected platform: " + platform.OSName())

	output.Info("Checking for Python 3.9+...")
	pythonCmd, pythonArgs, pythonVer := findPython()
	if pythonCmd == "" {
		output.Fail("Python 3.9+ not found. Install with: " + platform.PythonInstallHint())
	}
	output.Success(fmt.Sprintf("Found Python %s (%s)", pythonVer, pythonCmd))

	output.Info("Checking for pip...")
	pipArgs := append(pythonArgs, "-m", "pip", "--version")
	if _, err := runner.RunCapture(pythonCmd, pipArgs...); err != nil {
		output.Fail("pip not found. Install with: " + platform.PipInstallHint())
	}
	output.Success("pip available")

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

	output.Info("Checking data directory...")
	if platform.DirExists(dataDir) {
		output.Skip("Data directory at " + dataDir)
	} else {
		os.MkdirAll(dataDir, 0755)
		output.Success("Created data directory at " + dataDir)
	}

	output.Info("Setting up shell aliases...")
	knowledgeBin, _ := os.Executable()
	knowledgeBin, _ = filepath.EvalSymlinks(knowledgeBin)
	knowledgeBin, _ = filepath.Abs(knowledgeBin)
	setupShellAlias("knowledge", knowledgeBin)

	return backendPaths{
		ProjectDir: projectDir,
		VenvPython: venvPython,
		DataDir:    dataDir,
	}
}

// SetupClaudeCode wires the Knowledge MCP server into Claude Code: registers
// the server, adds permissions, installs hooks (SessionStart reminder +
// PreToolUse block on Read/Grep until search is called), and writes the
// managed Knowledge section into ~/.claude/CLAUDE.md.
func SetupClaudeCode() {
	output.Info("Checking for Claude Code CLI...")
	if claudePath, ok := runner.Exists("claude"); ok {
		output.Success("Claude Code CLI found: " + claudePath)
	} else {
		output.Fail("Claude Code CLI not found. Install it first: https://docs.anthropic.com/en/docs/claude-code/overview")
	}

	bp := setupBackend()

	output.Info("Checking MCP server registration...")
	if mcpOut, err := runner.RunCapture("claude", "mcp", "list"); err == nil && strings.Contains(mcpOut, "knowledge") {
		output.Skip("MCP server 'knowledge' already registered")
	} else {
		if mcpOut, err := runner.RunCapture("claude", "mcp", "list"); err == nil && strings.Contains(mcpOut, "mempalace") {
			runner.RunCapture("claude", "mcp", "remove", "mempalace")
		}
		output.Info("Registering MCP server with Claude Code (user scope)...")
		if err := runner.Run("claude", "mcp", "add", "--scope", "user",
			"knowledge",
			"-e", "PYTHONPATH="+bp.ProjectDir,
			"--", bp.VenvPython, "-m", "knowledgebase"); err != nil {
			output.Fail("Failed to register MCP server: " + err.Error())
		}
		output.Success("MCP server registered globally")
	}

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

	output.Info("Checking Claude Code hooks...")
	setupHooks(settingsPath, bp)

	output.Info("Checking global CLAUDE.md...")
	setupGlobalClaudeMD()

	output.Header("═══ Knowledge → Claude Code setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	fmt.Printf("  Python:      %s (%s)\n", venvPythonVer, bp.VenvPython)
	fmt.Printf("  Data:        %s\n", bp.DataDir)
	fmt.Printf("  MCP server:  knowledge (user scope)\n")
	fmt.Println()
	output.Info("Next steps:")
	fmt.Println("  1. Restart Claude Code to load the MCP server")
	fmt.Println("  2. Run /mcp in a session to verify knowledge tools are available")
	fmt.Println("  3. Use 'knowledge ingest <dir>' to index your project directories")
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

// setupHooks installs all Claude Code hooks needed for the knowledge MCP:
//
//   - Stop:        async transcript ingest + decision extraction
//   - PreCompact:  block + force a save before context compression
//   - SessionStart: inject reminder telling Claude to call wake_up + search
//   - PostToolUse(mcp__knowledge__search|wake_up): mark per-session sentinel
//   - PreToolUse(Read|Grep|Glob): block until the session sentinel exists,
//     forcing the model to call mcp__knowledge__search first
func setupHooks(settingsPath string, bp backendPaths) {
	venvPython := bp.VenvPython
	projectDir := bp.ProjectDir
	dataDir := bp.DataDir

	// Stop: index transcript + extract decisions (async, background).
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

	// PreCompact: tell Claude to flush before compression.
	preCompactCmd := `echo '{"decision":"block","reason":"COMPACTION IMMINENT. Save ALL topics, decisions, and important context from this session using the knowledge MCP tools (store, kg_add). Be thorough — after compaction, detailed context will be lost."}'`

	// SessionStart: inject a system reminder so Claude sees the contract
	// in fresh context (no CLAUDE.md drift after compaction).
	sessionStartCmd := `echo '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Knowledge MCP available. Call mcp__knowledge__wake_up to load prior context. Call mcp__knowledge__search BEFORE Read/Grep when answering context questions — Read/Grep will be blocked until you do."}}'`

	// PostToolUse on knowledge search/wake_up: write a per-session sentinel
	// so the PreToolUse gate on Read/Grep stops blocking.
	sentinelDir := filepath.Join(dataDir, ".sessions")
	postSearchCmd := fmt.Sprintf(
		`%s -c "
import json,sys,os,pathlib
try:
    d=json.loads(sys.stdin.read())
    sid=d.get('session_id','default')
    p=pathlib.Path(r'%s'); p.mkdir(parents=True,exist_ok=True)
    (p/sid).touch()
except Exception:
    pass
" 2>/dev/null`,
		venvPython, sentinelDir,
	)

	// PreToolUse on Read|Grep|Glob: block (exit 2) until the sentinel
	// exists for this session_id. Once mcp__knowledge__search or
	// mcp__knowledge__wake_up has run, Read/Grep flow normally.
	preReadCmd := fmt.Sprintf(
		`%s -c "
import json,sys,os,pathlib
try:
    d=json.loads(sys.stdin.read())
    sid=d.get('session_id','default')
    p=pathlib.Path(r'%s')/sid
    if p.exists():
        sys.exit(0)
    sys.stderr.write('Knowledge MCP gate: call mcp__knowledge__search (or mcp__knowledge__wake_up) before Read/Grep/Glob. The knowledge base may already have the answer.\n')
    sys.exit(2)
except SystemExit:
    raise
except Exception:
    sys.exit(0)
"`,
		venvPython, sentinelDir,
	)

	type hookSpec struct {
		event   string
		matcher string
		command string
		timeout int
		async   bool
	}

	hooks := []hookSpec{
		{"Stop", "", stopCmd, 120, true},
		{"PreCompact", "", preCompactCmd, 90, false},
		{"SessionStart", "startup|resume", sessionStartCmd, 10, false},
		{"PostToolUse", "mcp__knowledge__search|mcp__knowledge__wake_up", postSearchCmd, 10, true},
		{"PreToolUse", "Read|Grep|Glob", preReadCmd, 10, false},
	}

	for _, h := range hooks {
		if err := jsonutil.SetHookWithMatcher(settingsPath, h.event, h.matcher, h.command, h.timeout, h.async); err != nil {
			output.Warn("Could not set " + h.event + " hook: " + err.Error())
		} else {
			label := h.event
			if h.matcher != "" {
				label += "(" + h.matcher + ")"
			}
			output.Success("Configured " + label + " hook")
		}
	}
}

// setupGlobalClaudeMD writes the managed Knowledge section into
// ~/.claude/CLAUDE.md, preserving any other user-authored content. The
// section is bracketed with marker comments so re-running setup replaces
// only the managed block instead of clobbering the file.
func setupGlobalClaudeMD() {
	claudeDir := filepath.Join(platform.HomeDir(), ".claude")
	claudeMD := filepath.Join(claudeDir, "CLAUDE.md")

	if !platform.DirExists(claudeDir) {
		os.MkdirAll(claudeDir, 0755)
	}

	managed := instructions.MarkerStart + "\n" + instructions.ClaudeCodeCLAUDE + instructions.MarkerEnd + "\n"

	existing := ""
	if data, err := os.ReadFile(claudeMD); err == nil {
		existing = string(data)
	}

	updated := replaceManagedSection(existing, managed)
	if updated == existing {
		output.Skip("Global CLAUDE.md already up to date")
		return
	}

	if err := os.WriteFile(claudeMD, []byte(updated), 0644); err != nil {
		output.Warn("Could not write " + claudeMD + ": " + err.Error())
		return
	}
	output.Success("Updated " + claudeMD)
}

// replaceManagedSection swaps the marker-bracketed block in `existing` with
// `managed`. If no markers are present, appends the managed block (with a
// blank-line separator) so prior content is preserved.
func replaceManagedSection(existing, managed string) string {
	startIdx := strings.Index(existing, instructions.MarkerStart)
	endIdx := strings.Index(existing, instructions.MarkerEnd)
	if startIdx >= 0 && endIdx > startIdx {
		endIdx += len(instructions.MarkerEnd)
		// Eat the trailing newline if present so we don't accumulate blanks.
		if endIdx < len(existing) && existing[endIdx] == '\n' {
			endIdx++
		}
		return existing[:startIdx] + managed + existing[endIdx:]
	}
	if existing == "" {
		return managed
	}
	if !strings.HasSuffix(existing, "\n") {
		existing += "\n"
	}
	return existing + "\n" + managed
}
