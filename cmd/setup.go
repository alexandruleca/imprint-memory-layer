package cmd

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"runtime"

	"github.com/hunter/imprint/internal/gpustate"
	"github.com/hunter/imprint/internal/hooks"
	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// bundledPythonVersion is the interpreter uv downloads (via
// python-build-standalone) when no matching system Python is around. Pinned
// so every install ends up with the same minor — avoids the "it works on my
// machine" class of bugs across onnxruntime / tree-sitter wheels.
const bundledPythonVersion = "3.12"

// profileState is persisted at data/profile.json so re-running
// `imprint setup` without flags picks up the last explicit choice.
type profileState struct {
	Profile string `json:"profile"` // cpu | gpu | auto
	WithLLM bool   `json:"with_llm"`
}

func loadProfile(projectDir string) profileState {
	var st profileState
	data, err := os.ReadFile(platform.ProfileStatePath(projectDir))
	if err != nil {
		return st
	}
	_ = json.Unmarshal(data, &st)
	return st
}

func saveProfile(projectDir string, st profileState) {
	p := platform.ProfileStatePath(projectDir)
	if err := os.MkdirAll(filepath.Dir(p), 0755); err != nil {
		return
	}
	data, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return
	}
	_ = os.WriteFile(p, append(data, '\n'), 0644)
}

// createVenvWithUv creates (or reuses) the venv via bundled uv. uv
// auto-downloads python-build-standalone when no compatible system Python is
// available, so the user never has to install Python manually.
func createVenvWithUv(uv, venvDir, venvPython string) {
	// Fresh venv? Create it. uv venv is idempotent but re-runs always
	// replace, so only invoke when the python binary is actually missing
	// OR broken (stale path from a previous install, host Python that got
	// removed, etc.).
	//
	// --python-preference only-managed forces uv to use its own
	// python-build-standalone interpreter (installed under
	// UV_PYTHON_INSTALL_DIR set by setupBackend) and never reach for a
	// stray /usr/bin/python3.12 on the host — keeps Imprint's Python
	// 100% scoped to the install tree.
	needCreate := !platform.FileExists(venvPython)
	if !needCreate {
		// Existing venv — make sure its interpreter actually runs. A common
		// wedge after upgrading from the pre-uv era: the venv's pyvenv.cfg
		// points at a Homebrew Python that's been removed, so the symlink
		// resolves but the executable is dead.
		if _, err := runner.RunCapture(venvPython, "--version"); err != nil {
			output.Warn("Existing venv at " + venvDir + " is broken (" + err.Error() + ") — recreating")
			_ = os.RemoveAll(venvDir)
			needCreate = true
		}
	}
	if needCreate {
		output.Info("Creating virtual environment at " + venvDir + " (uv will download Python " + bundledPythonVersion + " if needed)...")
		if err := runner.Run(uv, "venv", venvDir,
			"--python", bundledPythonVersion,
			"--python-preference", "only-managed",
		); err != nil {
			output.Fail("uv venv failed: " + err.Error())
		}
		output.Success("Virtual environment ready")
	} else {
		output.Skip("Virtual environment at " + venvDir)
	}
}

// createVenvWithHostPython is the legacy fallback used when no uv binary is
// present — typically in a dev checkout where `make build && ./build/imprint
// setup` runs without the release-time fetch-uv step. Functionally identical
// to the pre-uv behaviour: detect host Python, `python -m venv`, then pip.
func createVenvWithHostPython(venvDir, venvPython, venvPip string) {
	output.Info(fmt.Sprintf("Checking for Python 3.%d–3.%d...", pythonMinMinor, pythonMaxMinor))
	py := findPython()
	if py.Cmd == "" && runtime.GOOS == "windows" && len(py.TooNew) == 0 {
		if os.Getenv("IMPRINT_NO_PYTHON_INSTALL") != "1" {
			if tryInstallPythonWindows() {
				py = findPython()
			}
		}
	}
	if py.Cmd == "" {
		if len(py.TooNew) > 0 {
			output.Fail(fmt.Sprintf(
				"Found Python %s but it's too new (need 3.%d–3.%d). Install: %s",
				strings.Join(py.TooNew, ", "), pythonMinMinor, pythonMaxMinor, platform.PythonInstallHint(),
			))
		}
		output.Fail(fmt.Sprintf(
			"No host Python found and no bundled uv. Install Python 3.%d–3.%d (%s) or ship the uv binary.",
			pythonMinMinor, pythonMaxMinor, platform.PythonInstallHint(),
		))
	}
	output.Success(fmt.Sprintf("Found Python %s (%s)", py.Version, py.Cmd))

	if !platform.FileExists(venvPython) {
		venvArgs := append(append([]string{}, py.ExtraArgs...), "-m", "venv", venvDir)
		if err := runner.Run(py.Cmd, venvArgs...); err != nil {
			output.Fail("venv creation failed: " + err.Error())
		}
		output.Success("Created venv at " + venvDir)
	} else {
		output.Skip("Virtual environment at " + venvDir)
	}

	_ = runner.Run(venvPip, "install", "--upgrade", "pip", "--quiet")
}

// ortRuntime is one way to provide the `onnxruntime` Python package. The
// distributions (onnxruntime, onnxruntime-gpu) install into the same package
// directory, so they cannot be layered: each runtime's requirements are a
// complete environment, base plus one overlay that contains exactly one of
// them (ADR 0032).
type ortRuntime struct {
	Name         string   // cpu | gpu
	Requirements []string // base + runtime overlay, locks preferred
	Distribution string   // the ONNX Runtime distribution the overlay installs
}

func ortRuntimes(projectDir string) map[string]ortRuntime {
	base := requirementsFile(projectDir, "base")
	return map[string]ortRuntime{
		"cpu": {Name: "cpu", Requirements: []string{base, requirementsFile(projectDir, "cpu")}, Distribution: "onnxruntime"},
		"gpu": {Name: "gpu", Requirements: []string{base, requirementsFile(projectDir, "gpu")}, Distribution: "onnxruntime-gpu"},
	}
}

// requirementsFile prefers the hash-locked file when the install ships it.
func requirementsFile(projectDir, name string) string {
	locked := filepath.Join(projectDir, "requirements", name+".lock.txt")
	if platform.FileExists(locked) {
		return locked
	}
	return filepath.Join(projectDir, "requirements", name+".txt")
}

// overlay is the runtime's own requirements file, the one after base.
func (rt ortRuntime) overlay() string {
	return rt.Requirements[len(rt.Requirements)-1]
}

// resolveRuntime maps an install profile to a runtime. The GPU runtime ships
// its CUDA libraries, so the host only needs a driver that supports their
// CUDA major; when the driver is known to be older, gpu and auto both use the
// CPU runtime (ADR 0038). auto also needs an NVIDIA GPU and no CUDA failure
// recorded for this host and GPU runtime (data/gpu_state.json).
func resolveRuntime(profile, dataDir, venvPython string, gpu ortRuntime) string {
	if profile == "cpu" {
		return "cpu"
	}
	if have, need := driverCUDAMajor(), lockedCUDAMajor(gpu.overlay()); have != 0 && have < need {
		output.Warn(fmt.Sprintf("The NVIDIA driver supports CUDA %d, but the GPU runtime needs CUDA %d: using CPU. Update the driver, then run `imprint setup` again.", have, need))
		return "cpu"
	}
	if profile == "gpu" {
		return "gpu"
	}
	env := ortGPUEnv(venvPython, gpu)
	if env.GPU == "" {
		return "cpu"
	}
	if state := gpustate.Load(dataDir); state.OrtGPU == "broken" && gpustate.SameEnv(state.OrtGPUEnv, env) {
		return "cpu"
	}
	return "gpu"
}

var (
	driverCUDARe = regexp.MustCompile(`CUDA Version:\s*(\d+)\.`)
	lockedCUDARe = regexp.MustCompile(`(?m)^nvidia-cuda-runtime==(\d+)\.`)
)

// driverCUDAMajor is the newest CUDA major the NVIDIA driver supports, from
// nvidia-smi's header, or 0 when there is no driver or it does not say.
func driverCUDAMajor() int {
	nvsmi, ok := runner.Exists("nvidia-smi")
	if !ok {
		return 0
	}
	out, _ := runner.RunCapture(nvsmi)
	if m := driverCUDARe.FindStringSubmatch(out); m != nil {
		major, _ := strconv.Atoi(m[1])
		return major
	}
	return 0
}

// lockedCUDAMajor is the CUDA major of the nvidia-cuda-runtime pin in a GPU
// requirements file, or 0 when it has none (an unlocked checkout).
func lockedCUDAMajor(requirements string) int {
	data, _ := os.ReadFile(requirements)
	if m := lockedCUDARe.FindSubmatch(data); m != nil {
		major, _ := strconv.Atoi(string(m[1]))
		return major
	}
	return 0
}

// ortProviders reports every installed distribution that provides the
// `onnxruntime` package and whether all files in its RECORD exist. It reads
// metadata only, so it works on a damaged runtime.
func ortProviders(venvPython, projectDir string) (map[string]bool, error) {
	out, err := runner.RunCaptureEnv(venvPython, []string{"PYTHONPATH=" + projectDir}, "-m", "imprint.onnx_runtime")
	if err != nil {
		return nil, fmt.Errorf("%v: %s", err, strings.TrimSpace(out))
	}
	providers := map[string]bool{}
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &providers); err != nil {
		return nil, fmt.Errorf("unexpected output %q: %v", out, err)
	}
	return providers, nil
}

// staleOrtProviders returns the distributions to uninstall before installing
// selected: every other provider of `onnxruntime`, and selected itself when
// its files are incomplete or when another provider is removed. Uninstalling
// one provider deletes files the others share while they still count as
// installed, and an installer skips a distribution it considers installed.
func staleOrtProviders(providers map[string]bool, selected string) []string {
	var stale []string
	selectedName, selectedIntact, selectedPresent := "", false, false
	for name, intact := range providers {
		if normalizeDistribution(name) == normalizeDistribution(selected) {
			selectedName, selectedIntact, selectedPresent = name, intact, true
		} else {
			stale = append(stale, name)
		}
	}
	if selectedPresent && (len(stale) > 0 || !selectedIntact) {
		stale = append(stale, selectedName)
	}
	sort.Strings(stale)
	return stale
}

var distributionSeparators = regexp.MustCompile(`[-_.]+`)

// normalizeDistribution applies the PEP 503 name normalization.
func normalizeDistribution(name string) string {
	return strings.ToLower(distributionSeparators.ReplaceAllString(name, "-"))
}

// installRuntime makes the venv match rt's complete requirements with
// rt.Distribution as the only, complete ONNX Runtime. A healthy venv that
// already matches is left untouched, so setup can run while Imprint holds
// the runtime's libraries open.
func installRuntime(uv, venvPython, venvPip, projectDir string, rt ortRuntime) {
	for _, requirements := range rt.Requirements {
		if !platform.FileExists(requirements) {
			output.Fail("requirements not found at " + requirements + " — is the install tree intact?")
		}
	}
	providers, err := ortProviders(venvPython, projectDir)
	if err != nil {
		output.Fail("Could not inspect the installed ONNX Runtime: " + err.Error())
	}
	if stale := staleOrtProviders(providers, rt.Distribution); len(stale) > 0 {
		output.Info("Removing " + strings.Join(stale, ", ") + " so " + rt.Distribution + " is installed alone and complete...")
		if err := pipUninstall(uv, venvPython, venvPip, stale...); err != nil {
			output.Fail("Removing the previous ONNX Runtime failed (stop Imprint and run setup again): " + err.Error())
		}
	}
	output.Info(fmt.Sprintf("Installing dependencies for the %s runtime (%s)...", rt.Name, rt.Distribution))
	if err := pipInstall(uv, venvPython, venvPip, requirementInstallArgs(rt.Requirements...)...); err != nil {
		output.Fail("Installing dependencies for the " + rt.Name + " runtime failed: " + err.Error())
	}
	if out, err := runner.RunCapture(venvPython, "-c", "import onnxruntime; onnxruntime.InferenceSession"); err != nil {
		output.Fail("ONNX Runtime does not load after installing " + rt.Distribution + ":\n" + lastNonBlankLines(out, 3))
	}
}

// installLLMOverlay installs the local LLM binding into an existing venv.
// The gpu and auto profiles install its GPU build (Vulkan on Windows and
// Linux x64, Metal on macOS) and keep it only when llama.cpp finds a GPU;
// otherwise the CPU build replaces it (ADR 0047). Both are the same version,
// so uv swaps them by wheel URL.
func installLLMOverlay(uv, venvPython, venvPip, projectDir, profile string) {
	if profile != "cpu" {
		installLlmReqs(uv, venvPython, venvPip, requirementsFile(projectDir, "llm-gpu"))
		if llamaOffloads(venvPython) {
			output.Success("Local LLM offloads to the GPU")
			return
		}
		output.Info("llama.cpp found no GPU it can use — installing its CPU build")
	}
	installLlmReqs(uv, venvPython, venvPip, requirementsFile(projectDir, "llm"))
}

// llamaOffloads reports whether the installed binding loads and has a GPU
// device: llama_supports_gpu_offload() asks llama.cpp's device registry, so
// it is false on a GPU build that found no usable GPU. A build whose Vulkan
// loader is missing fails to import.
func llamaOffloads(venvPython string) bool {
	out, err := runner.RunCapture(venvPython, "-c",
		"import llama_cpp.llama_cpp as ll; print(ll.llama_supports_gpu_offload())")
	return err == nil && strings.HasSuffix(strings.TrimSpace(out), "True")
}

// scopeUv scopes every uv invocation (including nested pip calls) to
// Imprint's install tree so the downloaded Python + wheel cache stay
// isolated from any other uv user on the machine. Uninstalling Imprint
// removes these dirs too. UV_PYTHON_INSTALL_DIR is respected by
// `uv python install` + implicit downloads done by `uv venv`; the wheel
// cache gets its own dir via UV_CACHE_DIR.
func scopeUv(projectDir string) {
	pyInstallDir := platform.UvPythonInstallDir(projectDir)
	uvCache := platform.UvCacheDir(projectDir)
	os.MkdirAll(pyInstallDir, 0755)
	os.MkdirAll(uvCache, 0755)
	os.Setenv("UV_PYTHON_INSTALL_DIR", pyInstallDir)
	os.Setenv("UV_CACHE_DIR", uvCache)
}

// resolveInstallProfile picks the install profile.
// Priority: explicit CLI flag > env var > persisted profile.json > auto.
func resolveInstallProfile(stored profileState) string {
	profile := strings.ToLower(strings.TrimSpace(installProfile))
	if profile == "" {
		profile = strings.ToLower(strings.TrimSpace(os.Getenv("IMPRINT_PROFILE")))
	}
	if profile == "" {
		profile = stored.Profile
	}
	if profile == "" {
		profile = "auto"
	}
	if profile != "cpu" && profile != "gpu" && profile != "auto" {
		output.Warn("Unknown profile '" + profile + "' — falling back to auto")
		profile = "auto"
	}
	return profile
}

// installLlmReqs layers one LLM overlay (llm or llm-gpu) on top of the base install.
func installLlmReqs(uv, venvPython, venvPip, llmReqs string) {
	if !platform.FileExists(llmReqs) {
		output.Warn("llm requirements not found at " + llmReqs + " — skipping LLM overlay")
		return
	}
	output.Info("Installing llama-cpp-python (local tagger + chat)...")
	if err := pipInstall(uv, venvPython, venvPip, requirementInstallArgs(llmReqs)...); err != nil {
		output.Warn("LLM overlay install failed: " + err.Error())
	}
}

// requirementInstallArgs resolves every file in one install, enforcing hashes
// when all of them are locks.
func requirementInstallArgs(requirements ...string) []string {
	var args []string
	locked := true
	for _, file := range requirements {
		args = append(args, "-r", file)
		locked = locked && strings.HasSuffix(file, ".lock.txt")
	}
	if locked {
		args = append(args, "--require-hashes")
	}
	return args
}

// pipInstall runs `pip install`-equivalent through uv when available,
// falling back to the venv-internal pip. uv is strongly preferred because
// `uv venv` does NOT seed a pip into the venv by default — so venvPip
// typically doesn't exist on post-uv installs, and every venvPip call
// silently fails. That used to leave broken onnxruntime-gpu stub wheels
// in place (AttributeError: no SessionOptions at import time).
func pipInstall(uv, venvPython, venvPip string, args ...string) error {
	if uv != "" {
		full := append([]string{"pip", "install", "--python", venvPython}, args...)
		return runner.Run(uv, full...)
	}
	full := append([]string{"install"}, args...)
	return runner.Run(venvPip, full...)
}

// pipUninstall removes packages from the venv, preferring uv.
func pipUninstall(uv, venvPython, venvPip string, pkgs ...string) error {
	if uv != "" {
		full := append([]string{"pip", "uninstall", "--python", venvPython}, pkgs...)
		return runner.Run(uv, full...)
	}
	full := append([]string{"uninstall", "-y"}, pkgs...)
	return runner.Run(venvPip, full...)
}

// uvBinary resolves the uv executable used for Python + wheel management.
// Order: bundled (<projectDir>/bin/uv) → `uv` on PATH → empty string. A
// missing uv is not fatal at this layer — setupBackend falls back to the
// legacy host-Python flow for local dev checkouts that don't ship uv.
func uvBinary(projectDir string) string {
	if p := platform.UvBinary(projectDir); platform.FileExists(p) {
		return p
	}
	if p, ok := runner.Exists("uv"); ok {
		return p
	}
	return ""
}

// retryGPU is set by main.go when the user passes --retry-gpu. When true,
// setupBackend clears data/gpu_state.json before the GPU helpers run so
// previously-sticky failures are retried.
var retryGPU bool

// SetRetryGPU lets main.go toggle the retry-on-failure flag.
func SetRetryGPU(v bool) { retryGPU = v }

// Install profile selected by the user (CLI flag, env var, or persisted
// state). Controls whether the GPU/LLM heavy deps are installed.
//
// Values: "cpu", "gpu", "auto" (default — detect NVIDIA GPU and pick).
// withLLM toggles the llama-cpp-python install (local tagger + chat).
// nonInteractiveInstall suppresses interactive fall-back prompts in setupBackend.
var (
	installProfile        string
	withLLM               *bool // tri-state: nil = unset (read from profile.json), else set
	nonInteractiveInstall bool
)

// SetInstallProfile records the profile flag passed on the CLI / from env.
// Empty string leaves the existing value alone.
func SetInstallProfile(v string) { installProfile = v }

// SetWithLLM explicitly enables or disables the llama-cpp-python install.
// Installers pass true when the user opted in on the wizard page.
func SetWithLLM(v bool) { b := v; withLLM = &b }

// SetNonInteractive disables interactive prompts during setupBackend. Used
// by installers that run in non-tty contexts (Inno [Run] hidden, macOS
// postinstall background job, CI).
func SetNonInteractive(v bool) { nonInteractiveInstall = v }

// backendPaths holds the resolved local paths produced by setupBackend.
// Every target (Claude Code, Cursor, ...) reuses these so the venv, data
// directory, and shell alias are installed once per machine.
type backendPaths struct {
	ProjectDir string
	VenvPython string
	DataDir    string
}

var preparedBackend *backendPaths

// setupBackend installs everything that is independent of the host AI tool:
// Python venv (via bundled uv), profile-selected deps, data directory,
// shell alias. Returns the resolved paths so target-specific code can wire
// them into MCP configs.
//
// Host Python is no longer a prerequisite: uv ships in the release archive
// and downloads its own Python via python-build-standalone when needed.
func setupBackend() backendPaths {
	if preparedBackend != nil {
		return *preparedBackend
	}
	projectDir := platform.FindProjectDir()
	venvDir := platform.VenvDir(projectDir)
	dataDir := platform.DataDir(projectDir)

	// When running from a read-only app bundle, mutable state (venv, data)
	// is redirected to ~/.local/share/imprint/. Ensure the parent exists.
	if mutableBase := platform.MutableBaseDir(projectDir); mutableBase != projectDir {
		os.MkdirAll(mutableBase, 0755)
	}

	scopeUv(projectDir)

	// ── Resolve install profile ────────────────────────────────
	stored := loadProfile(projectDir)
	profile := resolveInstallProfile(stored)
	var llmOn bool
	switch {
	case withLLM != nil:
		llmOn = *withLLM
	case os.Getenv("IMPRINT_WITH_LLM") != "":
		llmOn = os.Getenv("IMPRINT_WITH_LLM") == "1" || strings.EqualFold(os.Getenv("IMPRINT_WITH_LLM"), "true")
	default:
		llmOn = stored.WithLLM
	}

	output.Info("Detected platform: " + platform.OSName())
	output.Info(fmt.Sprintf("Install profile: %s%s", profile, map[bool]string{true: " + local LLM tagger", false: ""}[llmOn]))

	venvPython := platform.VenvPython(projectDir)
	venvPip := platform.VenvBin(projectDir, "pip")

	// ── Provision venv ─────────────────────────────────────────
	uv := uvBinary(projectDir)
	usedLegacyFallback := false
	if uv != "" {
		createVenvWithUv(uv, venvDir, venvPython)
	} else {
		// No bundled uv and no `uv` on PATH: dev checkout or broken install.
		// Fall back to the legacy host-Python + `python -m venv` + pip flow
		// so `make build && ./build/imprint setup` still works from source.
		output.Warn("bundled `uv` not found at " + platform.UvBinary(projectDir) + " — falling back to host Python (install `uv` for faster setups)")
		createVenvWithHostPython(venvDir, venvPython, venvPip)
		usedLegacyFallback = true
	}

	// If the user passed --retry-gpu, forget previous sticky failures so
	// this run re-attempts every CUDA path.
	if retryGPU {
		if err := gpustate.Clear(dataDir); err == nil {
			output.Info("--retry-gpu: cleared sticky GPU failure state")
		}
	}

	// ── Dependencies + ONNX Runtime ────────────────────────────
	// The profile selects one complete environment with exactly one ONNX
	// Runtime distribution. An explicit gpu profile installs onnxruntime-gpu
	// even if nvidia-smi is absent (e.g. a container where /dev/nvidia*
	// appears only at runtime); that runtime also runs on CPU.
	runtimes := ortRuntimes(projectDir)
	ort := runtimes[resolveRuntime(profile, dataDir, venvPython, runtimes["gpu"])]
	installRuntime(uv, venvPython, venvPip, projectDir, ort)
	if ort.Name == "gpu" && !checkOrtGPU(venvPython, dataDir, ort) && profile == "auto" {
		// The recorded failure makes auto resolve to CPU from now on;
		// install that now so the next run has nothing to change.
		installRuntime(uv, venvPython, venvPip, projectDir, runtimes["cpu"])
	}

	// Sanity check — the core runtime packages should import. If anything
	// core is missing we give up loud; the extractor/LLM extras are probed
	// later with graceful fallback.
	coreReq := []string{"fastmcp", "qdrant_client", "chonkie"}
	if stillMissing := checkPythonImports(venvPython, coreReq); len(stillMissing) > 0 {
		output.Fail("Core packages failed to install: " + strings.Join(stillMissing, ", "))
	}
	if out, err := runner.RunCapture(venvPython, "-m", "pip", "show", "fastmcp"); err == nil {
		output.Success("Core dependencies installed (fastmcp " + parsePackageVersion(out) + ")")
	} else {
		output.Success("Core dependencies installed")
	}

	// ── Optional local LLM tagger (llama-cpp-python) ───────────
	if llmOn {
		installLLMOverlay(uv, venvPython, venvPip, projectDir, profile)
	} else {
		output.Skip("Local LLM tagger skipped (--with-llm not set)")
	}

	// Persist the effective profile so subsequent runs without flags stay
	// consistent. Skip when we fell back to the legacy path — that run
	// didn't exercise uv, so we'd rather not lock the user in.
	if !usedLegacyFallback {
		saveProfile(projectDir, profileState{Profile: profile, WithLLM: llmOn})
	}

	// Native installer entry points mark first-run completion only after
	// both backend provisioning and host registration have finished.

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

	preparedBackend = &backendPaths{
		ProjectDir: projectDir,
		VenvPython: venvPython,
		DataDir:    dataDir,
	}
	return *preparedBackend
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
		output.Fail("Could not update " + settingsPath + ": " + err.Error())
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
	pythonMinMinor = 12 // locked runtime and safe archive extraction require 3.12
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

// ortSmokeScript builds a 1-op ONNX model in memory and runs it on CUDA. It
// prints "OK", or "ERR:<reason>" with the libraries ONNX Runtime's
// preload_dlls could not load, if any. The model declares IR version 8:
// helper.make_model otherwise stamps the installed onnx package's newest IR
// version, which can exceed what the installed runtime accepts.
const ortSmokeScript = `
import contextlib, io, sys
try:
    import onnxruntime as ort
    import numpy as np
    from onnx import helper, TensorProto
    preload = io.StringIO()
    with contextlib.redirect_stdout(preload):
        ort.preload_dlls()
    x = helper.make_tensor_value_info('x', TensorProto.FLOAT, [1])
    y = helper.make_tensor_value_info('y', TensorProto.FLOAT, [1])
    n = helper.make_node('Identity', ['x'], ['y'])
    g = helper.make_graph([n], 'g', [x], [y])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid('', 13)], ir_version=8)
    so = ort.SessionOptions(); so.log_severity_level = 4
    sess = ort.InferenceSession(m.SerializeToString(), sess_options=so, providers=['CUDAExecutionProvider'])
    if 'CUDAExecutionProvider' not in sess.get_providers():
        failed = [line for line in preload.getvalue().splitlines() if line.startswith('Failed to load')]
        raise RuntimeError('; '.join(failed) or 'CUDAExecutionProvider unavailable; session fell back to ' + ', '.join(sess.get_providers()))
    sess.run(None, {'x': np.zeros(1, dtype=np.float32)})
    print('OK')
except Exception as e:
    line = str(e).splitlines()[0] if str(e) else type(e).__name__
    print('ERR:' + line)
    sys.exit(1)
`

// ortSmokeTest runs the smoke script and returns (ok, reason). It reads only
// stdout: ONNX Runtime logs to stderr, in UTF-16 on Windows.
func ortSmokeTest(venvPython string) (bool, string) {
	out, err := runner.CommandWithEnv(venvPython, []string{"-c", ortSmokeScript}).Output()
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "OK" {
			return true, ""
		}
		if reason, found := strings.CutPrefix(line, "ERR:"); found {
			return false, reason
		}
	}
	if err != nil {
		return false, err.Error()
	}
	return false, "no result"
}

// ortHostEnv fingerprints the host for the sticky-failure cache so a stored
// "broken" verdict is discarded when the GPU, driver, or Python version
// changes.
func ortHostEnv(venvPython string) gpustate.Env {
	env := gpustate.Env{Python: pythonTag(venvPython)}
	if nvsmi, ok := runner.Exists("nvidia-smi"); ok {
		if name, err := runner.RunCapture(nvsmi, "--query-gpu=name", "--format=csv,noheader"); err == nil {
			env.GPU = strings.TrimSpace(strings.Split(name, "\n")[0])
		}
		if drv, err := runner.RunCapture(nvsmi, "--query-gpu=driver_version", "--format=csv,noheader"); err == nil {
			env.Driver = strings.TrimSpace(strings.Split(drv, "\n")[0])
		}
	}
	return env
}

// ortGPUEnv is ortHostEnv plus the GPU runtime the CUDA verdict is measured
// on, so a new GPU lock retries CUDA.
func ortGPUEnv(venvPython string, gpu ortRuntime) gpustate.Env {
	env := ortHostEnv(venvPython)
	if data, err := os.ReadFile(gpu.overlay()); err == nil {
		env.Runtime = fmt.Sprintf("%x", sha256.Sum256(data))[:16]
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

// checkOrtGPU smoke-tests CUDA on the installed GPU runtime and records the
// verdict for this host and GPU runtime in data/gpu_state.json; auto uses
// the CPU runtime while a failure is recorded. It never swaps packages:
// onnxruntime-gpu also runs on CPU.
func checkOrtGPU(venvPython, dataDir string, gpu ortRuntime) bool {
	state := gpustate.Load(dataDir)
	state.OrtGPUEnv = ortGPUEnv(venvPython, gpu)
	ok, reason := ortSmokeTest(venvPython)
	if ok {
		output.Success("GPU acceleration available (CUDA) — " + state.OrtGPUEnv.GPU)
		state.OrtGPU, state.OrtGPUReason = "ok", ""
	} else {
		output.Warn("CUDA smoke test failed (" + reason + ") — embeddings run on CPU")
		state.OrtGPU, state.OrtGPUReason = "broken", reason
	}
	_ = gpustate.Save(dataDir, state)
	return ok
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
//   - PostToolUse(mcp__imprint__search): mark per-session sentinel
//   - PreToolUse(Read|Grep|Glob): block until the session sentinel exists,
//     forcing the model to call mcp__imprint__search first
func setupHooks(settingsPath string, bp backendPaths) {
	hp := hooks.Paths{ProjectDir: bp.ProjectDir, VenvPython: bp.VenvPython, DataDir: bp.DataDir}

	type hookSpec struct {
		event   string
		matcher string
		command string
		timeout int
		async   bool
	}

	specs := []hookSpec{
		{"Stop", "", hooks.StopCommand(hp), 120, true},
		{"PreCompact", "", hooks.PreCompactCommand(hp), 90, false},
		{"SessionStart", "startup|resume", hooks.SessionStartCommand(hp), 10, false},
		{"PostToolUse", "mcp__imprint__search", hooks.PostSearchSentinelCommand(hp), 10, true},
		{"PreToolUse", "Read|Grep|Glob", hooks.PreReadGateCommand(hp), 10, false},
	}

	for _, h := range specs {
		if err := jsonutil.SetOwnedHookWithMatcher(settingsPath, h.event, h.matcher, h.command, h.timeout, h.async); err != nil {
			output.Fail("Could not set " + h.event + " hook: " + err.Error())
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

	updated := instructions.MergeManaged(existing, managed)
	if updated == existing {
		output.Skip("Global CLAUDE.md already up to date")
		return
	}

	if err := os.WriteFile(claudeMD, []byte(updated), 0644); err != nil {
		output.Fail("Could not write " + claudeMD + ": " + err.Error())
		return
	}
	output.Success("Updated " + claudeMD)
}

// replaceManagedSection is preserved as a thin alias over
// instructions.MergeManaged for backward source compatibility with older
// callers in this package.
func replaceManagedSection(existing, managed string) string {
	return instructions.MergeManaged(existing, managed)
}
