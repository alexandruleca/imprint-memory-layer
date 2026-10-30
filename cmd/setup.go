package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"

	"runtime"

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
// `imprint setup` without flags picks up the last explicit choice. Chosen
// marks a profile the user picked (flag, env, `imprint profile set`); a
// profile recorded without it, as installers before it recorded their
// non-interactive cpu default, is re-resolved as auto.
type profileState struct {
	Profile string `json:"profile"`          // cpu | gpu | auto
	Chosen  bool   `json:"chosen,omitempty"` // picked by the user, not a default
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

// requirementsFile prefers the hash-locked file when the install ships it.
func requirementsFile(projectDir, name string) string {
	locked := filepath.Join(projectDir, "requirements", name+".lock.txt")
	if platform.FileExists(locked) {
		return locked
	}
	return filepath.Join(projectDir, "requirements", name+".txt")
}

// lastLine is the last non-empty line of s: the JSON a Python helper prints
// after anything its imports wrote.
func lastLine(s string) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	return strings.TrimSpace(lines[len(lines)-1])
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

// installCoreRuntime makes the venv hold the base requirements with the CPU
// build of ONNX Runtime, which OCR and the privacy filter use, as its only,
// complete ONNX Runtime (ADR 0032). It removes another build left from
// before embeddings moved to llama.cpp, with what only that build brought
// in (ADR 0086). Both builds write the same files, so removing one or
// repairing the CPU build frees the runtime first (ADR 0084). A healthy venv
// that already matches is left untouched.
func installCoreRuntime(uv, venvPython, venvPip, projectDir string) {
	requirements := []string{requirementsFile(projectDir, "base"), requirementsFile(projectDir, "cpu")}
	for _, path := range requirements {
		if !platform.FileExists(path) {
			output.Fail("requirements not found at " + path + " — is the install tree intact?")
		}
	}
	// Asked before installing, while the build being removed still says
	// what it brought in.
	unused := unusedDistributions(venvPython, projectDir)
	if providers, err := ortProviders(venvPython, projectDir); err == nil && len(staleOrtProviders(providers, cpuOrt)) > 0 {
		freeRuntime()
	}
	install := func() {
		if err := pipInstall(uv, venvPython, venvPip, requirementInstallArgs(requirements...)...); err != nil {
			output.Fail("Installing dependencies failed: " + err.Error())
		}
	}
	output.Info("Installing dependencies...")
	install()
	providers, err := ortProviders(venvPython, projectDir)
	if err != nil {
		output.Fail("Could not inspect the installed ONNX Runtime: " + err.Error())
	}
	stale := staleOrtProviders(providers, cpuOrt)
	if len(stale) > 0 {
		output.Info("Removing " + strings.Join(stale, ", ") + " so " + cpuOrt + " is installed alone and complete...")
		if err := pipUninstall(uv, venvPython, venvPip, stale...); err != nil {
			output.Fail("Removing the previous ONNX Runtime failed (stop Imprint and run setup again): " + err.Error())
		}
		install()
	}
	removeDistributions(uv, venvPython, venvPip, unused, stale)
	if out, err := runner.RunCapture(venvPython, "-c", "import onnxruntime; onnxruntime.InferenceSession"); err != nil {
		output.Fail("ONNX Runtime does not load after installing " + cpuOrt + ":\n" + lastNonBlankLines(out, 3))
	}
}

// cpuOrt is the ONNX Runtime distribution Imprint installs.
const cpuOrt = "onnxruntime"

// unusedDistributions asks imprint.onnx_runtime which installed
// distributions Imprint no longer uses: ONNX Runtime builds other than the
// CPU one, what only they need, and libraries an older lock brought in.
// When it cannot tell, nothing is removed.
func unusedDistributions(venvPython, projectDir string) []string {
	out, err := runner.RunCaptureEnv(venvPython, []string{"PYTHONPATH=" + projectDir}, "-m", "imprint.onnx_runtime", "unused")
	var names []string
	if err == nil {
		err = json.Unmarshal([]byte(lastLine(out)), &names)
	}
	if err != nil {
		output.Warn("Could not tell which packages Imprint no longer uses, so none are removed: " + lastNonBlankLines(out, 2))
		return nil
	}
	return names
}

// removeDistributions uninstalls names, except the providers already
// removed, and drops each of them from uv's cache: uv links the venv's files
// to its cache, so only then is their disk space free.
func removeDistributions(uv, venvPython, venvPip string, names, removed []string) {
	if len(names) == 0 {
		return
	}
	output.Info("Removing packages Imprint no longer uses: " + strings.Join(names, ", "))
	var remove []string
	for _, name := range names {
		if !slices.ContainsFunc(removed, func(r string) bool { return normalizeDistribution(r) == name }) {
			remove = append(remove, name)
		}
	}
	if len(remove) > 0 {
		if err := pipUninstall(uv, venvPython, venvPip, remove...); err != nil {
			output.Warn("Removing them failed: " + err.Error())
			return
		}
	}
	if uv == "" {
		return
	}
	for _, name := range names { // One at a time: uv stops at the first file it cannot delete.
		if out, err := runner.RunCapture(uv, "cache", "clean", name); err != nil {
			output.Warn("Could not remove " + name + " from uv's cache: " + lastNonBlankLines(out, 1))
		}
	}
}

// installLlama installs llama.cpp's binding, which runs embeddings and the
// local LLM (ADR 0086). The gpu and auto profiles install its GPU build
// (Vulkan on Windows and Linux x64, Metal on macOS) and keep it only when
// llama.cpp finds a GPU; otherwise the CPU build replaces it (ADR 0049).
// Both are the same version, so uv swaps them by wheel URL.
func installLlama(uv, venvPython, venvPip, projectDir, profile string) {
	if profile != "cpu" {
		installLlamaBuild(uv, venvPython, venvPip, requirementsFile(projectDir, "llm-gpu"))
		if llamaOffloads(venvPython) {
			output.Success("llama.cpp runs on the GPU")
			return
		}
		output.Info("llama.cpp found no GPU it can use — installing its CPU build")
	}
	installLlamaBuild(uv, venvPython, venvPip, requirementsFile(projectDir, "llm"))
	if out, err := runner.RunCapture(venvPython, "-c", "import llama_cpp"); err != nil {
		output.Fail("llama.cpp does not load after installing:\n" + lastNonBlankLines(out, 3))
	}
	output.Success("llama.cpp runs on the CPU")
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
// Priority: explicit CLI flag > env var > profile.json the user chose > auto.
// A stored profile nobody chose is a default, so setup tries the GPU again.
func resolveInstallProfile(stored profileState) profileState {
	profile := strings.ToLower(strings.TrimSpace(installProfile))
	if profile == "" {
		profile = strings.ToLower(strings.TrimSpace(os.Getenv("IMPRINT_PROFILE")))
	}
	chosen := profile != ""
	if !chosen && stored.Chosen {
		profile, chosen = stored.Profile, true
	}
	if !chosen && stored.Profile == "cpu" {
		output.Info("The cpu profile was an installer default — trying llama.cpp's GPU build (imprint profile set cpu keeps the CPU)")
	}
	if profile == "" {
		profile = "auto"
	}
	if profile != "cpu" && profile != "gpu" && profile != "auto" {
		output.Warn("Unknown profile '" + profile + "' — falling back to auto")
		profile, chosen = "auto", false
	}
	return profileState{Profile: profile, Chosen: chosen}
}

// installLlamaBuild installs one build of the binding (llm or llm-gpu) on top
// of the base install.
func installLlamaBuild(uv, venvPython, venvPip, requirements string) {
	if !platform.FileExists(requirements) {
		output.Fail("requirements not found at " + requirements + " — is the install tree intact?")
	}
	output.Info("Installing llama.cpp (embeddings, local tagger and chat)...")
	if err := pipInstall(uv, venvPython, venvPip, requirementInstallArgs(requirements)...); err != nil {
		output.Fail("Installing llama.cpp failed: " + err.Error())
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
//
// uv's dry run says first whether the install changes the venv and whether
// it replaces or removes installed packages, which frees the runtime
// (ADR 0084). Adding packages does not: no process has their files loaded.
func pipInstall(uv, venvPython, venvPip string, args ...string) error {
	if uv != "" {
		full := append([]string{"pip", "install", "--python", venvPython}, args...)
		plan, err := runner.RunCapture(uv, append(full, "--dry-run")...)
		switch {
		case err == nil && strings.Contains(plan, "Would make no changes"):
			return nil
		case err == nil && strings.Contains(plan, "Would uninstall"):
			freeRuntime()
		}
		return runner.Run(uv, full...)
	}
	freeRuntime()
	full := append([]string{"install"}, args...)
	return runner.Run(venvPip, full...)
}

// pipUninstall removes packages from the venv, preferring uv.
func pipUninstall(uv, venvPython, venvPip string, pkgs ...string) error {
	freeRuntime()
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

// Install profile selected by the user (CLI flag, env var, or persisted
// state). It selects llama.cpp's build: "cpu" installs the CPU build; "gpu"
// and "auto" (the default) install the GPU build and keep it when llama.cpp
// finds a GPU (ADR 0049, 0086).
// nonInteractiveInstall suppresses interactive fall-back prompts in setupBackend.
var (
	installProfile        string
	nonInteractiveInstall bool
)

// SetInstallProfile records the profile flag passed on the CLI / from env.
// Empty string leaves the existing value alone.
func SetInstallProfile(v string) { installProfile = v }

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
	resolved := resolveInstallProfile(loadProfile(projectDir))
	profile := resolved.Profile

	output.Info("Detected platform: " + platform.OSName())
	output.Info("Install profile: " + profile)

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

	// ── Dependencies, ONNX Runtime and llama.cpp ───────────────
	// One complete environment with exactly one ONNX Runtime distribution,
	// the CPU build (ADR 0032), and llama.cpp, which runs embeddings and the
	// local LLM; the install profile selects llama.cpp's build (ADR 0086).
	// Setup changes the venv once running jobs and setups are done.
	holdRuntime(projectDir)
	defer releaseRuntime()
	setupStep(1, 3, "installing dependencies")
	installCoreRuntime(uv, venvPython, venvPip, projectDir)

	// Sanity check — the core runtime packages should import. If anything
	// core is missing we give up loud; the extractor/LLM extras are probed
	// later with graceful fallback.
	coreReq := []string{"fastmcp", "qdrant_client", "chonkie", "rapidocr", "pypdfium2", "PIL"}
	if stillMissing := checkPythonImports(venvPython, coreReq); len(stillMissing) > 0 {
		output.Fail("Core packages failed to install: " + strings.Join(stillMissing, ", "))
	}
	if out, err := runner.RunCapture(venvPython, "-m", "pip", "show", "fastmcp"); err == nil {
		output.Success("Core dependencies installed (fastmcp " + parsePackageVersion(out) + ")")
	} else {
		output.Success("Core dependencies installed")
	}

	setupStep(2, 3, "installing llama.cpp")
	installLlama(uv, venvPython, venvPip, projectDir, profile)

	// Persist the effective profile so subsequent runs without flags stay
	// consistent. Skip when we fell back to the legacy path — that run
	// didn't exercise uv, so we'd rather not lock the user in.
	if !usedLegacyFallback {
		saveProfile(projectDir, resolved)
	}

	// Native installer entry points mark first-run completion only after
	// both backend provisioning and host registration have finished.

	// Extractor self-check — surfaces config + OCR prereqs clearly.
	setupStep(3, 3, "checking document extractors")
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
	installLauncher(projectDir, imprintBin)

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
	prepareDesktopApp(projectDir)

	preparedBackend = &backendPaths{
		ProjectDir: projectDir,
		VenvPython: venvPython,
		DataDir:    dataDir,
	}
	return *preparedBackend
}

// installedBackend is the runtime setup provisioned, as it is: connect
// registers it with clients without installing or replacing anything, so it
// is safe while Imprint runs from that runtime (the dashboard, MCP servers).
func installedBackend() backendPaths {
	projectDir := platform.FindProjectDir()
	bp := backendPaths{ProjectDir: projectDir, VenvPython: platform.VenvPython(projectDir), DataDir: platform.DataDir(projectDir)}
	if !platform.FileExists(bp.VenvPython) {
		output.Fail("Imprint's runtime is not installed (" + bp.VenvPython + " is missing). Run imprint setup first.")
	}
	return bp
}

// SetupClaudeCode wires the Imprint MCP server into Claude Code: registers
// the server, adds permissions, installs hooks (SessionStart reminder +
// PreToolUse block on Read/Grep until search is called), and writes the
// managed Imprint section into ~/.claude/CLAUDE.md.
func SetupClaudeCode(backend func() backendPaths) {
	output.Info("Checking for Claude Code CLI...")
	if claudePath, ok := runner.Exists("claude"); ok {
		output.Success("Claude Code CLI found: " + claudePath)
	} else {
		output.Warn("Claude Code CLI not found — install it first: https://docs.anthropic.com/en/docs/claude-code/overview. Skipping.")
		return
	}
	setupHostsRan++

	bp := backend()

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
// are available after setup, including the default local OCR runtime.
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
    ('audio', 'faster_whisper', '.wav/.mp3/.m4a/.flac and more'),
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

# The default OCR models and PDF renderer are base runtime wheels.
from imprint.config_schema import resolve
from imprint.extractors.ocr import available
if bool(resolve("ingest.ocr_enabled")[0]):
    lang = str(resolve("ingest.ocr_lang")[0])
    if available(lang):
        print("OCR: enabled (local models + PDF renderer installed)")
    else:
        print("OCR: enabled BUT runtime missing — run imprint bootstrap; custom languages need Tesseract language data")
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
