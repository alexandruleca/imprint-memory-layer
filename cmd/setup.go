package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"runtime"

	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
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

	output.Info(fmt.Sprintf("Checking for Python 3.%d–3.%d...", pythonMinMinor, pythonMaxMinor))
	py := findPython()
	if py.Cmd == "" {
		if len(py.TooNew) > 0 {
			output.Fail(fmt.Sprintf(
				"Found Python %s but it's too new — some dependencies don't support it yet (need 3.%d–3.%d).\n    Install a compatible version: %s",
				strings.Join(py.TooNew, ", "), pythonMinMinor, pythonMaxMinor, platform.PythonInstallHint(),
			))
		}
		output.Fail(fmt.Sprintf(
			"No compatible Python found (need 3.%d–3.%d). Install with: %s",
			pythonMinMinor, pythonMaxMinor, platform.PythonInstallHint(),
		))
	}
	output.Success(fmt.Sprintf("Found Python %s (%s)", py.Version, py.Cmd))

	output.Info("Checking for pip...")
	pipArgs := append(append([]string{}, py.ExtraArgs...), "-m", "pip", "--version")
	if _, err := runner.RunCapture(py.Cmd, pipArgs...); err != nil {
		output.Fail("pip not found. Install with: " + platform.PipInstallHint())
	}
	output.Success("pip available")

	venvPython := platform.VenvPython(projectDir)
	venvPip := platform.VenvBin(projectDir, "pip")

	output.Info("Setting up virtual environment...")
	venvHealthy := false
	if platform.DirExists(venvDir) {
		// Verify the venv python binary works AND matches the version we found.
		// A venv created with an older Python will "work" but pip won't install
		// packages that require the newer version.
		venvOut, err := runner.RunCapture(venvPython, "--version")
		if err == nil {
			venvMatch := pythonVersionRe.FindStringSubmatch(venvOut)
			pyMatch := pythonVersionRe.FindStringSubmatch("Python " + py.Version)
			if venvMatch != nil && pyMatch != nil &&
				venvMatch[1] == pyMatch[1] && venvMatch[2] == pyMatch[2] {
				venvHealthy = true
				output.Skip("Virtual environment at " + venvDir)
			} else {
				venvVer := "unknown"
				if venvMatch != nil {
					venvVer = venvMatch[1] + "." + venvMatch[2]
				}
				output.Warn(fmt.Sprintf(
					"Virtual environment uses Python %s but found %s — recreating...",
					venvVer, py.Version,
				))
				os.RemoveAll(venvDir)
			}
		} else {
			output.Warn("Virtual environment at " + venvDir + " is broken, recreating...")
			os.RemoveAll(venvDir)
		}
	}
	if !venvHealthy {
		venvArgs := append(append([]string{}, py.ExtraArgs...), "-m", "venv", venvDir)
		if err := runner.Run(py.Cmd, venvArgs...); err != nil {
			output.Fail("Failed to create virtual environment: " + err.Error())
		}
		output.Success("Created virtual environment at " + venvDir)
	}

	output.Info("Upgrading pip...")
	if err := runner.Run(venvPip, "install", "--upgrade", "pip", "--quiet"); err != nil {
		output.Warn("Could not upgrade pip: " + err.Error())
	} else {
		output.Success("pip up to date")
	}

	output.Info("Checking dependencies...")
	if !platform.FileExists(requirementsFile) {
		output.Fail("requirements.txt not found at " + requirementsFile + " — is the project directory correct?")
	}
	// Probe a representative set — core runtime + document extractors + URL
	// fetch. Missing any = re-run `pip install -r requirements.txt`. Catches
	// the common case where an older imprint install predates the doc
	// ingestion feature: fastmcp is present but pypdf/httpx/trafilatura
	// aren't.
	requiredPkgs := []string{
		"fastmcp",       // MCP runtime
		"qdrant_client", // vector store
		"onnxruntime",   // embeddings
		"chonkie",       // chunker
		"pypdf",         // .pdf extractor
		"docx",          // .docx (python-docx exposes `docx`)
		"pptx",          // .pptx (python-pptx exposes `pptx`)
		"openpyxl",      // .xlsx
		"ebooklib",      // .epub
		"striprtf",      // .rtf
		"bs4",           // html/epub fallback
		"httpx",         // URL fetch
		"trafilatura",   // html readability
	}
	missing := checkPythonImports(venvPython, requiredPkgs)
	if len(missing) == 0 {
		if out, err := runner.RunCapture(venvPip, "show", "fastmcp"); err == nil {
			output.Skip("Dependencies installed (fastmcp " + parsePackageVersion(out) + ")")
		} else {
			output.Skip("Dependencies installed")
		}
	} else {
		output.Info(fmt.Sprintf("Installing dependencies (missing: %s)...", strings.Join(missing, ", ")))
		if err := runner.Run(venvPip, "install", "-r", requirementsFile, "--quiet"); err != nil {
			output.Fail("Failed to install dependencies: " + err.Error())
		}
		// Re-verify after install. If still missing, something in
		// requirements.txt couldn't be resolved for this Python/platform.
		stillMissing := checkPythonImports(venvPython, requiredPkgs)
		if len(stillMissing) > 0 {
			output.Warn("After install still missing: " + strings.Join(stillMissing, ", ") +
				" — some doc formats will be skipped at ingest time")
		} else {
			output.Success("Dependencies installed")
		}
	}

	// Extractor self-check — surfaces config + OCR prereqs clearly.
	reportExtractorHealth(venvPython, projectDir, dataDir)

	output.Info("Checking data directory...")
	if platform.DirExists(dataDir) {
		output.Skip("Data directory at " + dataDir)
	} else {
		os.MkdirAll(dataDir, 0755)
		output.Success("Created data directory at " + dataDir)
	}

	output.Info("Setting up shell aliases...")
	imprintBin := bundledBinaryPath(projectDir)
	if imprintBin == "" {
		// Fallback: use the running executable itself
		imprintBin, _ = os.Executable()
		imprintBin, _ = filepath.EvalSymlinks(imprintBin)
		imprintBin, _ = filepath.Abs(imprintBin)
	}
	setupShellAlias("imprint", imprintBin)

	return backendPaths{
		ProjectDir: projectDir,
		VenvPython: venvPython,
		DataDir:    dataDir,
	}
}

// SetupClaudeCode wires the Imprint MCP server into Claude Code: registers
// the server, adds permissions, installs hooks (SessionStart reminder +
// PreToolUse block on Read/Grep until search is called), and writes the
// managed Imprint section into ~/.claude/CLAUDE.md.
func SetupClaudeCode() {
	output.Info("Checking for Claude Code CLI...")
	if claudePath, ok := runner.Exists("claude"); ok {
		output.Success("Claude Code CLI found: " + claudePath)
	} else {
		output.Fail("Claude Code CLI not found. Install it first: https://docs.anthropic.com/en/docs/claude-code/overview")
	}

	bp := setupBackend()

	output.Info("Checking MCP server registration...")
	if mcpOut, err := runner.RunCapture("claude", "mcp", "list"); err == nil && strings.Contains(mcpOut, "imprint") {
		output.Skip("MCP server 'imprint' already registered")
	} else {
		if mcpOut, err := runner.RunCapture("claude", "mcp", "list"); err == nil && strings.Contains(mcpOut, "mempalace") {
			runner.RunCapture("claude", "mcp", "remove", "mempalace")
		}
		output.Info("Registering MCP server with Claude Code (user scope)...")
		if err := runner.Run("claude", "mcp", "add", "--scope", "user",
			"imprint",
			"-e", "PYTHONPATH="+bp.ProjectDir,
			"--", bp.VenvPython, "-m", "imprint"); err != nil {
			output.Fail("Failed to register MCP server: " + err.Error())
		}
		output.Success("MCP server registered globally")
	}

	output.Info("Checking Claude Code permissions...")
	settingsPath := platform.ClaudeSettingsPath()
	added, err := jsonutil.EnsurePermission(settingsPath, "mcp__imprint__*")
	if err != nil {
		output.Warn("Could not update " + settingsPath + ": " + err.Error())
	} else if added {
		output.Success("Added imprint permissions to " + settingsPath)
	} else {
		output.Skip("imprint permissions already configured")
	}

	output.Info("Checking Claude Code hooks...")
	setupHooks(settingsPath, bp)

	output.Info("Checking global CLAUDE.md...")
	setupGlobalClaudeMD()

	output.Header("═══ Imprint → Claude Code setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	fmt.Printf("  Python:      %s (%s)\n", venvPythonVer, bp.VenvPython)
	fmt.Printf("  Data:        %s\n", bp.DataDir)
	fmt.Printf("  MCP server:  imprint (user scope)\n")
	fmt.Println()
	output.Info("Next steps:")
	fmt.Println("  1. Restart Claude Code to load the MCP server")
	fmt.Println("  2. Run /mcp in a session to verify imprint tools are available")
	fmt.Println("  3. Use 'imprint ingest <dir>' to index your project directories")
}

const (
	pythonMinMinor = 10 // minimum supported: 3.10
	pythonMaxMinor = 13 // maximum supported: 3.13 (ONNX Runtime, etc.)
)

var pythonVersionRe = regexp.MustCompile(`Python (\d+)\.(\d+)\.(\d+)`)

type pythonSearchResult struct {
	Cmd       string
	ExtraArgs []string
	Version   string
	TooNew    []string // deduplicated version strings found but above max
}

func findPython() pythonSearchResult {
	var result pythonSearchResult
	tooNewSeen := make(map[string]bool)

	for _, candidate := range platform.PythonCandidates() {
		args := append(append([]string{}, candidate.ExtraArgs...), "--version")
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
		ver := matches[1] + "." + matches[2] + "." + matches[3]

		if major == 3 && minor > pythonMaxMinor {
			if !tooNewSeen[ver] {
				result.TooNew = append(result.TooNew, ver)
				tooNewSeen[ver] = true
			}
			continue
		}
		if major > 3 || (major == 3 && minor >= pythonMinMinor) {
			result.Cmd = candidate.Cmd
			result.ExtraArgs = candidate.ExtraArgs
			result.Version = ver
			return result
		}
	}

	return result
}

// checkPythonImports probes each package via `python -c "import X"`.
// Returns the list that could NOT be imported. Cheap way to detect a
// partial/outdated install without walking requirements.txt.
func checkPythonImports(venvPython string, pkgs []string) []string {
	var missing []string
	for _, pkg := range pkgs {
		if _, err := runner.RunCapture(venvPython, "-c", "import "+pkg); err != nil {
			missing = append(missing, pkg)
		}
	}
	return missing
}

// reportExtractorHealth prints a human summary of which document formats
// are available after setup, and surfaces OCR prereqs (system tesseract
// binary + pillow/pytesseract) when ingest.ocr_enabled is true.
func reportExtractorHealth(venvPython, projectDir, dataDir string) {
	script := `
import sys
sys.path.insert(0, r'` + projectDir + `')
import importlib
checks = [
    ('pdf',  'pypdf',       '.pdf'),
    ('docx', 'docx',        '.docx'),
    ('pptx', 'pptx',        '.pptx'),
    ('xlsx', 'openpyxl',    '.xlsx'),
    ('epub', 'ebooklib',    '.epub'),
    ('rtf',  'striprtf',    '.rtf'),
    ('html', 'trafilatura', '.html/.htm'),
    ('url',  'httpx',       '<url>'),
]
ok, missing = [], []
for name, mod, ext in checks:
    try:
        importlib.import_module(mod)
        ok.append(f"{name} ({ext})")
    except Exception:
        missing.append(f"{name} ({ext}) — pip install {mod}")
print("OK:", ", ".join(ok) if ok else "none")
if missing:
    print("MISSING:")
    for m in missing:
        print("  -", m)

# OCR prereqs (optional)
from imprint.config_schema import resolve
ocr = bool(resolve("ingest.ocr_enabled")[0])
if ocr:
    try:
        import pytesseract, PIL  # noqa
        import shutil as _sh
        tess = _sh.which("tesseract")
        if tess:
            print(f"OCR: enabled (tesseract at {tess})")
        else:
            print("OCR: enabled BUT system tesseract binary missing — install via your package manager")
    except Exception as e:
        print(f"OCR: enabled BUT python deps missing ({e}) — pip install pillow pytesseract pdf2image")
else:
    print("OCR: disabled (imprint config set ingest.ocr_enabled true to enable)")
`
	output.Info("Checking extractor health...")
	out, err := runner.RunCaptureEnv(venvPython,
		[]string{"PYTHONPATH=" + projectDir, "IMPRINT_DATA_DIR=" + dataDir},
		"-c", script,
	)
	if err != nil {
		output.Warn("Could not verify extractors: " + err.Error())
		return
	}
	for _, line := range strings.Split(strings.TrimRight(out, "\n"), "\n") {
		if line == "" {
			continue
		}
		switch {
		case strings.HasPrefix(line, "OK:"):
			output.Success(line)
		case strings.HasPrefix(line, "MISSING:"):
			output.Warn(line)
		case strings.HasPrefix(line, "  -"):
			fmt.Println("    " + line)
		case strings.HasPrefix(line, "OCR: enabled BUT"):
			output.Warn(line)
		case strings.HasPrefix(line, "OCR:"):
			output.Info(line)
		default:
			fmt.Println("  " + line)
		}
	}
}

func parsePackageVersion(pipShowOutput string) string {
	for _, line := range strings.Split(pipShowOutput, "\n") {
		if strings.HasPrefix(line, "Version:") {
			return strings.TrimSpace(strings.TrimPrefix(line, "Version:"))
		}
	}
	return "unknown"
}

// bundledBinaryPath returns the absolute path to the platform-specific imprint
// binary in the project's bin/ directory. Returns "" if not found.
func bundledBinaryPath(projectDir string) string {
	ext := ""
	if runtime.GOOS == "windows" {
		ext = ".exe"
	}
	name := fmt.Sprintf("imprint-%s-%s%s", runtime.GOOS, runtime.GOARCH, ext)
	p := filepath.Join(projectDir, "bin", name)
	if platform.FileExists(p) {
		return p
	}
	return ""
}

func setupShellAlias(name, targetPath string) {
	shellName, rcFile := platform.DetectShell()
	if rcFile == "" {
		output.Warn("Unknown shell '" + shellName + "' — add alias manually: alias " + name + "=\"" + targetPath + "\"")
		return
	}

	aliasLine := platform.AliasLine(shellName, name, targetPath)

	dir := filepath.Dir(rcFile)
	if !platform.DirExists(dir) {
		os.MkdirAll(dir, 0755)
	}

	// Read existing content to check for stale alias
	data, _ := os.ReadFile(rcFile)
	content := string(data)

	searchPattern := platform.AliasSearchPattern(shellName, name)
	if strings.Contains(content, searchPattern) {
		// Alias exists — check if it already points to the right target
		if strings.Contains(content, aliasLine) {
			output.Skip(name + " alias in " + rcFile)
			return
		}
		// Stale alias — replace it
		updated := platform.ReplaceAliasBlock(content, name, aliasLine)
		if err := os.WriteFile(rcFile, []byte(updated), 0644); err != nil {
			output.Warn("Could not update " + rcFile + ": " + err.Error())
			return
		}
		output.Success("Updated " + name + " alias in " + rcFile)
		return
	}

	// No alias yet — append
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

// setupHooks installs all Claude Code hooks needed for the imprint MCP:
//
//   - Stop:        async transcript ingest + decision extraction
//   - PreCompact:  block + force a save before context compression
//   - SessionStart: inject reminder telling Claude to call wake_up + search
//   - PostToolUse(mcp__imprint__search|wake_up): mark per-session sentinel
//   - PreToolUse(Read|Grep|Glob): block until the session sentinel exists,
//     forcing the model to call mcp__imprint__search first
func setupHooks(settingsPath string, bp backendPaths) {
	venvPython := bp.VenvPython
	projectDir := bp.ProjectDir
	dataDir := bp.DataDir

	// Stop: index transcript + extract decisions (async, background).
	stopCmd := fmt.Sprintf(
		`PYTHONPATH=%s IMPRINT_DATA_DIR=%s %s -c "
import json,sys,subprocess,os
d=json.loads(sys.stdin.read())
tp=d.get('transcript_path','')
if tp:
    subprocess.run([sys.executable,'-m','imprint.cli_conversations','--transcript',tp],env=os.environ)
    subprocess.run([sys.executable,'-m','imprint.cli_extract',tp],env=os.environ)
" 2>/dev/null`,
		projectDir, dataDir, venvPython,
	)

	// PreCompact: tell Claude to flush before compression.
	preCompactCmd := `echo '{"decision":"block","reason":"COMPACTION IMMINENT. Save ALL topics, decisions, and important context from this session using the imprint MCP tools (store, kg_add). Be thorough — after compaction, detailed context will be lost."}'`

	// SessionStart: inject a system reminder so Claude sees the contract
	// in fresh context (no CLAUDE.md drift after compaction).
	sessionStartCmd := `echo '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Imprint MCP available. Call mcp__imprint__wake_up to load prior context. Call mcp__imprint__search BEFORE Read/Grep when answering context questions — Read/Grep will be blocked until you do."}}'`

	// PostToolUse on imprint search/wake_up: write a per-session sentinel
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
	// exists for this session_id. Once mcp__imprint__search or
	// mcp__imprint__wake_up has run, Read/Grep flow normally.
	preReadCmd := fmt.Sprintf(
		`%s -c "
import json,sys,os,pathlib
try:
    d=json.loads(sys.stdin.read())
    sid=d.get('session_id','default')
    p=pathlib.Path(r'%s')/sid
    if p.exists():
        sys.exit(0)
    sys.stderr.write('Imprint MCP gate: call mcp__imprint__search (or mcp__imprint__wake_up) before Read/Grep/Glob. The knowledge base may already have the answer.\n')
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
		{"PostToolUse", "mcp__imprint__search|mcp__imprint__wake_up", postSearchCmd, 10, true},
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

// setupGlobalClaudeMD writes the managed Imprint section into
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
