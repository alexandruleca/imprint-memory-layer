package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"runtime"

	"github.com/hunter/imprint/internal/gpustate"
	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// retryGPU is set by main.go when the user passes --retry-gpu. When true,
// setupBackend clears data/gpu_state.json before the GPU helpers run so
// previously-sticky failures are retried.
var retryGPU bool

// SetRetryGPU lets main.go toggle the retry-on-failure flag.
func SetRetryGPU(v bool) { retryGPU = v }

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
		// Only need system pip/venv when creating a fresh venv
		output.Info("Checking for pip...")
		pipArgs := append(append([]string{}, py.ExtraArgs...), "-m", "pip", "--version")
		if _, err := runner.RunCapture(py.Cmd, pipArgs...); err != nil {
			// Try ensurepip as fallback before giving up
			ensureArgs := append(append([]string{}, py.ExtraArgs...), "-m", "ensurepip", "--default-pip")
			if err2 := runner.Run(py.Cmd, ensureArgs...); err2 != nil {
				output.Fail("pip not found. Install with: " + platform.PipInstallHint())
			}
		}

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
		"llama_cpp",     // local Gemma chat + tagger
		"fastapi",       // dashboard API server
		"uvicorn",       // ASGI server for FastAPI
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

	// ── GPU acceleration ────────────────────────────────────────
	// If the user passed --retry-gpu, forget previous sticky failures so
	// this run re-attempts every CUDA install path.
	if retryGPU {
		if err := gpustate.Clear(dataDir); err == nil {
			output.Info("--retry-gpu: cleared sticky GPU failure state")
		}
	}

	// If an NVIDIA GPU is present, swap onnxruntime (CPU) for onnxruntime-gpu.
	// They conflict — can't have both installed.
	ensureOrtGPU(venvPython, venvPip, dataDir)

	// If an NVIDIA GPU is present, rebuild llama-cpp-python with CUDA
	// support so the local tagger + chat use GPU offload.
	ensureLlamaCppGPU(venvPython, venvPip, projectDir, dataDir)

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

	// ── Dashboard UI (pre-built static export) ─────────────────
	// The Next.js dashboard is pre-built and shipped as static HTML/CSS/JS
	// in imprint/ui/out/. No Node.js or npm needed at runtime.
	// Developers rebuild with: cd imprint/ui && npm install && npm run build
	uiOut := filepath.Join(projectDir, "imprint", "ui", "out")
	if platform.DirExists(uiOut) {
		output.Skip("Dashboard UI bundled (static)")
	} else {
		output.Warn("Dashboard UI not found at imprint/ui/out/ — run `cd imprint/ui && npm install && npm run build` to rebuild")
	}

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
		output.Warn("Claude Code CLI not found — install it first: https://docs.anthropic.com/en/docs/claude-code/overview. Skipping.")
		return
	}
	setupHostsRan++

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

// ortSmokeScript builds a 1-op ONNX model in memory and tries to run it on
// CUDA. Writes "OK" on success or "ERR:<first line>" on failure. Used to
// detect the "provider listed but runtime libs missing" case that the
// static `get_available_providers()` check can't catch (e.g. libcublasLt.so.12
// missing when onnxruntime-gpu links CUDA 12 but the host runs CUDA 13).
const ortSmokeScript = `
import sys
try:
    from imprint.embeddings import _preload_cuda_libs
    _preload_cuda_libs()
except Exception:
    pass
try:
    import onnxruntime as ort
    import numpy as np
    from onnx import helper, TensorProto
    x = helper.make_tensor_value_info('x', TensorProto.FLOAT, [1])
    y = helper.make_tensor_value_info('y', TensorProto.FLOAT, [1])
    n = helper.make_node('Identity', ['x'], ['y'])
    g = helper.make_graph([n], 'g', [x], [y])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid('', 13)])
    so = ort.SessionOptions(); so.log_severity_level = 4
    sess = ort.InferenceSession(m.SerializeToString(), sess_options=so, providers=['CUDAExecutionProvider'])
    sess.run(None, {'x': np.zeros(1, dtype=np.float32)})
    print('OK')
except Exception as e:
    line = str(e).splitlines()[0] if str(e) else type(e).__name__
    print('ERR:' + line)
    sys.exit(1)
`

// ortSmokeTest runs the smoke script and returns (ok, firstErrorLine). A
// missing `onnx` package counts as a smoke failure whose error text will
// start with "No module named 'onnx'" — callers install it alongside the
// cu12 wheels.
func ortSmokeTest(venvPython, projectDir string) (bool, string) {
	out, err := runner.RunCaptureEnv(venvPython, []string{"PYTHONPATH=" + projectDir},
		"-c", ortSmokeScript)
	if err == nil && strings.Contains(out, "OK") {
		return true, ""
	}
	// Pick the last ERR: line (there may be ORT log noise before it).
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "ERR:") {
			return false, strings.TrimPrefix(line, "ERR:")
		}
	}
	if out != "" {
		return false, strings.Split(strings.TrimSpace(out), "\n")[0]
	}
	if err != nil {
		return false, err.Error()
	}
	return false, "unknown"
}

// cudaRuntimeMissing reports whether the smoke error string is one of the
// well-known "missing runtime library" cases that `_preload_cuda_libs`
// can fix by installing the matching cu12 pip wheels.
func cudaRuntimeMissing(errLine string) bool {
	needles := []string{
		"libcublasLt", "libcublas", "libcudart",
		"libcudnn", "libcufft", "libcurand",
	}
	for _, n := range needles {
		if strings.Contains(errLine, n) {
			return true
		}
	}
	return false
}

// ortHostEnv fingerprints the host for the sticky-failure cache so a stored
// "broken" verdict is discarded when the GPU, driver, or Python version
// changes.
func ortHostEnv(venvPython string) gpustate.Env {
	env := gpustate.Env{Python: pythonTag(venvPython)}
	if nvsmi, ok := runner.Exists("nvidia-smi"); ok {
		if name, err := runner.RunCapture(nvsmi, "--query-gpu=name", "--format=csv,noheader"); err == nil {
			env.GPU = strings.Split(strings.TrimSpace(name), "\n")[0]
		}
		if drv, err := runner.RunCapture(nvsmi, "--query-gpu=driver_version", "--format=csv,noheader"); err == nil {
			env.Driver = strings.Split(strings.TrimSpace(drv), "\n")[0]
		}
	}
	return env
}

// pythonTag returns the major.minor Python version of the given interpreter
// (e.g. "3.13"), or "" if the interpreter can't be queried. Used in the
// gpustate fingerprint so cached verdicts invalidate across Python upgrades.
func pythonTag(venvPython string) string {
	if venvPython == "" {
		return ""
	}
	out, err := runner.RunCapture(venvPython, "--version")
	if err != nil {
		return ""
	}
	m := pythonVersionRe.FindStringSubmatch(out)
	if m == nil {
		return ""
	}
	return m[1] + "." + m[2]
}

// ortStubWheelErr reports whether the smoke error looks like the broken
// "empty-namespace" wheel case — onnxruntime-gpu installs a package with only
// capi/ and no real Python bindings, so `import onnxruntime` succeeds but
// `SessionOptions` / `InferenceSession` are missing. Observed on cp313 wheels
// of onnxruntime-gpu 1.24.x.
// lastNonBlankLines returns the last n non-blank lines of s, joined with
// newlines and prefixed with two spaces for readable indent under a warn.
// Used to surface compiler/pip error tails without flooding the console.
func lastNonBlankLines(s string, n int) string {
	if s == "" || n <= 0 {
		return ""
	}
	raw := strings.Split(s, "\n")
	var kept []string
	for i := len(raw) - 1; i >= 0 && len(kept) < n; i-- {
		line := strings.TrimRight(raw[i], " \t\r")
		if strings.TrimSpace(line) == "" {
			continue
		}
		kept = append([]string{"  " + line}, kept...)
	}
	return strings.Join(kept, "\n")
}

func ortStubWheelErr(errLine string) bool {
	return strings.Contains(errLine, "has no attribute 'SessionOptions'") ||
		strings.Contains(errLine, "has no attribute 'InferenceSession'") ||
		strings.Contains(errLine, "has no attribute \"SessionOptions\"") ||
		strings.Contains(errLine, "has no attribute \"InferenceSession\"")
}

// ensureOrtGPU checks for an NVIDIA GPU and swaps onnxruntime (CPU) for
// onnxruntime-gpu if needed. On top of the swap it runs an end-to-end smoke
// test (CUDAExecutionProvider can actually construct a session), installs
// the cu12 pip wheels if the first failure looks like a missing runtime
// lib, and caches "broken" verdicts in data/gpu_state.json so repeat setup
// runs stay silent instead of reinstalling every time.
func ensureOrtGPU(venvPython, venvPip, dataDir string) {
	state := gpustate.Load(dataDir)
	hostEnv := ortHostEnv(venvPython)

	// Skip re-work if a previous run already proved this env is broken.
	if state.OrtGPU == "broken" && gpustate.SameEnv(state.OrtGPUEnv, hostEnv) && hostEnv.GPU != "" {
		output.Skip("ORT GPU previously unhealthy on this host — using CPU (run `imprint setup --retry-gpu` to retry)")
		return
	}

	// Fast path: provider registered AND smoke test passes = nothing to do.
	hasCuda, cudaErr := runner.RunCapture(venvPython, "-c",
		"import onnxruntime as ort; assert hasattr(ort, 'SessionOptions'), 'broken'; print('CUDAExecutionProvider' in ort.get_available_providers())")
	if cudaErr == nil && strings.TrimSpace(hasCuda) == "True" {
		projectDir := platform.FindProjectDir()
		if ok, _ := ortSmokeTest(venvPython, projectDir); ok {
			output.Skip("GPU acceleration active (CUDA)")
			state.OrtGPU = "ok"
			state.OrtGPUEnv = hostEnv
			_ = gpustate.Save(dataDir, state)
			return
		}
		// Provider lists CUDA but can't actually create a session — fall
		// through so we install cu12 wheels and retry below.
	}

	// If the module won't even import, reinstall CPU version and continue.
	if cudaErr != nil {
		output.Warn("onnxruntime module is broken — reinstalling CPU version...")
		_ = runner.Run(venvPip, "uninstall", "onnxruntime-gpu", "onnxruntime", "-y", "--quiet")
		_ = runner.Run(venvPip, "install", "onnxruntime", "--quiet")
	}

	// No GPU? CPU is fine.
	nvsmi, hasNvsmi := runner.Exists("nvidia-smi")
	if !hasNvsmi || hostEnv.GPU == "" {
		output.Skip("No NVIDIA GPU detected — using CPU for embeddings")
		return
	}
	_ = nvsmi

	output.Info(fmt.Sprintf("NVIDIA GPU found: %s — installing onnxruntime-gpu...", hostEnv.GPU))

	_ = runner.Run(venvPip, "uninstall", "onnxruntime", "-y", "--quiet")
	if err := runner.Run(venvPip, "install", "onnxruntime-gpu", "onnx", "--quiet"); err != nil {
		output.Warn("Failed to install onnxruntime-gpu: " + err.Error() + " — falling back to CPU")
		_ = runner.Run(venvPip, "install", "onnxruntime", "--quiet")
		state.OrtGPU = "broken"
		state.OrtGPUReason = err.Error()
		state.OrtGPUEnv = hostEnv
		_ = gpustate.Save(dataDir, state)
		return
	}

	projectDir := platform.FindProjectDir()
	ok, errLine := ortSmokeTest(venvPython, projectDir)
	if !ok && cudaRuntimeMissing(errLine) {
		output.Info("Missing CUDA runtime libs — installing cu12 pip wheels...")
		_ = runner.Run(venvPip, "install", "--quiet",
			"nvidia-cuda-runtime-cu12", "nvidia-cublas-cu12", "nvidia-cudnn-cu12",
			"nvidia-cufft-cu12", "nvidia-curand-cu12")
		ok, errLine = ortSmokeTest(venvPython, projectDir)
	}

	if ok {
		output.Success("GPU acceleration enabled (CUDA) — " + hostEnv.GPU)
		state.OrtGPU = "ok"
		state.OrtGPUReason = ""
		state.OrtGPUEnv = hostEnv
		_ = gpustate.Save(dataDir, state)
		return
	}

	if ortStubWheelErr(errLine) {
		output.Warn(fmt.Sprintf(
			"onnxruntime-gpu wheel for Python %s ships no Python bindings (installs as empty namespace) — falling back to CPU. Use Python 3.12 for GPU acceleration until upstream ships a working cp%s wheel.",
			hostEnv.Python, strings.ReplaceAll(hostEnv.Python, ".", ""),
		))
	} else {
		output.Warn("onnxruntime-gpu smoke test failed (" + errLine + ") — falling back to CPU")
	}
	_ = runner.Run(venvPip, "uninstall", "onnxruntime-gpu", "-y", "--quiet")
	_ = runner.Run(venvPip, "install", "onnxruntime", "--quiet")
	state.OrtGPU = "broken"
	state.OrtGPUReason = errLine
	state.OrtGPUEnv = hostEnv
	_ = gpustate.Save(dataDir, state)
}

// nvccVersion returns the major.minor of the installed nvcc, or "" if nvcc
// isn't on PATH. Example outputs: "12.6", "13.0".
func nvccVersion() string {
	nvcc, ok := runner.Exists("nvcc")
	if !ok {
		return ""
	}
	out, err := runner.RunCapture(nvcc, "--version")
	if err != nil {
		return ""
	}
	re := regexp.MustCompile(`release (\d+)\.(\d+)`)
	m := re.FindStringSubmatch(out)
	if m == nil {
		return ""
	}
	return m[1] + "." + m[2]
}

// cudaPath returns the CUDA toolkit root (parent of bin/nvcc), falling back
// to /usr/local/cuda for Linux defaults.
func cudaPath() string {
	if nvcc, ok := runner.Exists("nvcc"); ok {
		if resolved, err := filepath.EvalSymlinks(nvcc); err == nil {
			return filepath.Dir(filepath.Dir(resolved))
		}
		return filepath.Dir(filepath.Dir(nvcc))
	}
	return "/usr/local/cuda"
}

// computeCap returns the reported compute capability of GPU 0, e.g. "12.0"
// (Blackwell) or "8.6" (Ampere). Empty string on failure.
func computeCap() string {
	nvsmi, ok := runner.Exists("nvidia-smi")
	if !ok {
		return ""
	}
	out, err := runner.RunCapture(nvsmi, "--query-gpu=compute_cap", "--format=csv,noheader")
	if err != nil {
		return ""
	}
	return strings.Split(strings.TrimSpace(out), "\n")[0]
}

// archFlagFor turns a compute_cap like "12.0" into the CMAKE_CUDA_ARCHITECTURES
// value ("120") the CMake CUDA backend expects. Returns a broad fallback list
// if the cap can't be parsed, so older/unknown GPUs still build.
func archFlagFor(cap string) string {
	cap = strings.TrimSpace(cap)
	if cap == "" {
		return "75;80;86;89;90"
	}
	parts := strings.Split(cap, ".")
	if len(parts) != 2 {
		return "75;80;86;89;90"
	}
	return parts[0] + parts[1]
}

// nvccSupportsCap reports whether the installed nvcc can generate SASS for
// the given compute capability. Blackwell (sm_120) needs nvcc 12.8+. Unknown
// versions get the benefit of the doubt — we let the rebuild try and fail.
func nvccSupportsCap(nvcc, cap string) bool {
	if nvcc == "" || cap == "" {
		return true
	}
	var nvMajor, nvMinor int
	if _, err := fmt.Sscanf(nvcc, "%d.%d", &nvMajor, &nvMinor); err != nil {
		return true
	}
	var capMajor, capMinor int
	if _, err := fmt.Sscanf(cap, "%d.%d", &capMajor, &capMinor); err != nil {
		return true
	}
	// Blackwell: needs nvcc >= 12.8
	if capMajor >= 12 {
		return nvMajor > 12 || (nvMajor == 12 && nvMinor >= 8)
	}
	// Hopper sm_90: nvcc 12.x ok. Older: anything reasonable.
	return true
}

// ensureLlamaCppGPU rebuilds llama-cpp-python with CUDA offload when a
// compatible NVIDIA GPU + toolchain is present. Remembers failures per
// {gpu, nvcc, compute_cap} in gpu_state.json so repeat setup runs don't
// spam the same retry-and-fail cycle.
func ensureLlamaCppGPU(venvPython, venvPip, projectDir, dataDir string) {
	// Quick check: is llama_cpp even installed?
	if _, err := runner.RunCapture(venvPython, "-c", "import llama_cpp"); err != nil {
		return
	}

	// Already has GPU offload? Clear any stale sentinel and move on.
	if hasGPU, _ := runner.RunCapture(venvPython, "-c",
		"import llama_cpp.llama_cpp as ll; print(ll.llama_supports_gpu_offload())"); strings.TrimSpace(hasGPU) == "True" {
		output.Skip("llama-cpp-python has GPU offload (CUDA)")
		if st := gpustate.Load(dataDir); st.LlamaCudaFailed != nil {
			st.LlamaCudaFailed = nil
			_ = gpustate.Save(dataDir, st)
		}
		return
	}

	// No NVIDIA GPU → CPU is fine.
	nvsmi, hasNvsmi := runner.Exists("nvidia-smi")
	if !hasNvsmi {
		output.Skip("llama-cpp-python using CPU (no NVIDIA GPU)")
		return
	}
	gpuName, err := runner.RunCapture(nvsmi, "--query-gpu=name", "--format=csv,noheader")
	if err != nil || strings.TrimSpace(gpuName) == "" {
		output.Skip("llama-cpp-python using CPU (no NVIDIA GPU)")
		return
	}
	gpuShort := strings.Split(strings.TrimSpace(gpuName), "\n")[0]
	cap := computeCap()
	nvcc := nvccVersion()
	currentEnv := gpustate.Env{GPU: gpuShort, Nvcc: nvcc, ComputeCap: cap, Python: pythonTag(venvPython)}

	// Sticky skip: same env already failed once.
	state := gpustate.Load(dataDir)
	if state.LlamaCudaFailed != nil && gpustate.SameEnv(state.LlamaCudaFailed.Env, currentEnv) {
		output.Skip("llama-cpp CUDA rebuild skipped — previous attempt failed for this GPU/nvcc combo (imprint setup --retry-gpu to retry)")
		return
	}

	// No nvcc on PATH → can't rebuild with CUDA. Warn with actionable hint
	// instead of attempting a doomed compile (which produces a bare
	// "exit status 1" and no useful diagnostic).
	if nvcc == "" {
		output.Warn(fmt.Sprintf(
			"llama-cpp CUDA rebuild skipped — nvcc not on PATH. Install CUDA Toolkit 12.8+ (or add its bin dir to PATH) to enable GPU offload for %s (sm_%s).",
			gpuShort, archFlagFor(cap),
		))
		state.LlamaCudaFailed = &gpustate.LlamaCudaFail{Env: currentEnv, TS: gpustate.Now()}
		_ = gpustate.Save(dataDir, state)
		return
	}

	// Nvcc too old for this compute cap? Warn once and bail instead of
	// burning minutes on a rebuild that cannot produce valid SASS.
	if !nvccSupportsCap(nvcc, cap) {
		output.Warn(fmt.Sprintf(
			"llama-cpp CUDA rebuild skipped — %s (sm_%s) requires nvcc 12.8+, found %s",
			gpuShort, archFlagFor(cap), nvcc,
		))
		state.LlamaCudaFailed = &gpustate.LlamaCudaFail{Env: currentEnv, TS: gpustate.Now()}
		_ = gpustate.Save(dataDir, state)
		return
	}

	arch := archFlagFor(cap)
	cudaRoot := cudaPath()
	output.Info(fmt.Sprintf("NVIDIA GPU found: %s (sm_%s) — rebuilding llama-cpp-python with CUDA (nvcc %s, %s)...",
		gpuShort, arch, nvcc, cudaRoot))

	env := []string{
		fmt.Sprintf("CMAKE_ARGS=-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=%s", arch),
		"CUDA_PATH=" + cudaRoot,
	}
	if out, err := runner.RunCaptureEnv(venvPip, env,
		"install", "llama-cpp-python", "--force-reinstall", "--no-cache-dir"); err != nil {
		output.Warn("Failed to rebuild llama-cpp-python with CUDA: " + err.Error() + " — using CPU")
		if tail := lastNonBlankLines(out, 5); tail != "" {
			output.Warn("pip/compile tail:\n" + tail)
		}
		state.LlamaCudaFailed = &gpustate.LlamaCudaFail{Env: currentEnv, TS: gpustate.Now()}
		_ = gpustate.Save(dataDir, state)
		// Try to restore a working CPU build so the tagger still loads.
		_ = runner.Run(venvPip, "install", "llama-cpp-python", "--force-reinstall", "--no-cache-dir", "--quiet")
		return
	}

	check, checkErr := runner.RunCapture(venvPython, "-c",
		"import llama_cpp.llama_cpp as ll; print(ll.llama_supports_gpu_offload())")
	if checkErr != nil {
		output.Warn("llama-cpp-python CUDA build broken (can't import) — reinstalling CPU version")
		_ = runner.Run(venvPip, "install", "llama-cpp-python", "--force-reinstall", "--no-cache-dir", "--quiet")
		state.LlamaCudaFailed = &gpustate.LlamaCudaFail{Env: currentEnv, TS: gpustate.Now()}
		_ = gpustate.Save(dataDir, state)
		return
	}
	if strings.TrimSpace(check) == "True" {
		output.Success("llama-cpp-python rebuilt with CUDA — " + gpuShort)
		state.LlamaCudaFailed = nil
		_ = gpustate.Save(dataDir, state)
		return
	}
	output.Warn("llama-cpp-python rebuilt but GPU offload still unavailable — check CUDA toolkit")
	state.LlamaCudaFailed = &gpustate.LlamaCudaFail{Env: currentEnv, TS: gpustate.Now()}
	_ = gpustate.Save(dataDir, state)
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
