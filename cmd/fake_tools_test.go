package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// TestMain lets the test binary stand in for uv, the venv's pip and its
// python: those receive flags the testing package would otherwise reject.
func TestMain(m *testing.M) {
	if log := os.Getenv("IMPRINT_FAKE_TOOL_LOG"); log != "" {
		os.Exit(fakeTool(log, os.Args[1:]))
	}
	os.Exit(m.Run())
}

// fakeTool records each call as one line in log and keeps the venv's
// installed distributions in IMPRINT_FAKE_VENV (a JSON list), so callers see
// installs and removals take effect:
//
//	install <requirements files>   uv pip install / pip install
//	uninstall <distributions>      uv pip uninstall / pip uninstall
//	cache-clean <distribution>     uv cache clean
//	cache-prune                    uv cache prune
//	plan [--retry] | check         python -m imprint.gpu_runtime
//
// The plan is IMPRINT_FAKE_PLAN, the CUDA check passes when
// IMPRINT_FAKE_CHECK is "ok", IMPRINT_FAKE_UNUSED maps each runtime to what
// it does not use, and llama.cpp's offload answer is IMPRINT_FAKE_OFFLOAD.
func fakeTool(log string, args []string) int {
	record := func(line string) {
		f, err := os.OpenFile(log, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err == nil {
			fmt.Fprintln(f, line)
			f.Close()
		}
	}
	if len(args) > 1 && args[0] == "pip" { // uv pip <verb> --python <venv python> ...
		args = append([]string{args[1]}, args[4:]...)
	}
	switch {
	case len(args) > 0 && args[0] == "install":
		var files []string
		installed := fakeVenv()
		for i, arg := range args {
			if arg == "-r" {
				files = append(files, filepath.Base(args[i+1]))
				for _, name := range fakeRequirementNames(args[i+1]) {
					installed[name] = true
				}
			}
		}
		saveFakeVenv(installed)
		record("install " + strings.Join(files, " "))
	case len(args) > 0 && args[0] == "uninstall":
		installed := fakeVenv()
		var names []string
		for _, name := range args[1:] {
			if name != "-y" {
				delete(installed, normalizeDistribution(name))
				names = append(names, name)
			}
		}
		saveFakeVenv(installed)
		record("uninstall " + strings.Join(names, " "))
	case len(args) > 1 && args[0] == "cache" && args[1] == "clean":
		record("cache-clean " + strings.Join(args[2:], " "))
	case len(args) > 1 && args[0] == "cache" && args[1] == "prune":
		record("cache-prune")
	case len(args) > 2 && args[0] == "-m" && args[1] == "imprint.gpu_runtime":
		record(strings.Join(args[2:], " "))
		if args[2] == "plan" {
			fmt.Println(os.Getenv("IMPRINT_FAKE_PLAN"))
			return 0
		}
		if os.Getenv("IMPRINT_FAKE_CHECK") == "ok" {
			fmt.Println(`{"ok": true, "detail": "NVIDIA GeForce RTX 4080 Laptop GPU"}`)
			return 0
		}
		fmt.Println(`{"ok": false, "detail": "cuDNN is unavailable"}`)
		return 1
	case len(args) > 3 && args[0] == "-m" && args[1] == "imprint.onnx_runtime" && args[2] == "unused":
		// What imprint.onnx_runtime would name: the installed distributions
		// IMPRINT_FAKE_UNUSED lists for the selected runtime.
		var only map[string][]string
		_ = json.Unmarshal([]byte(os.Getenv("IMPRINT_FAKE_UNUSED")), &only)
		installed := fakeVenv()
		unused := []string{}
		for _, name := range only[args[3]] {
			if installed[name] {
				unused = append(unused, name)
			}
		}
		data, _ := json.Marshal(unused)
		fmt.Println(string(data))
	case len(args) > 1 && args[0] == "-m" && args[1] == "imprint.onnx_runtime":
		providers := map[string]bool{}
		for name := range fakeVenv() {
			if name == "onnxruntime" || name == "onnxruntime-gpu" {
				providers[name] = true
			}
		}
		data, _ := json.Marshal(providers)
		fmt.Println(string(data))
	case len(args) > 1 && args[0] == "-c" && strings.Contains(args[1], "importlib.metadata"):
		var names []string
		for name := range fakeVenv() {
			names = append(names, name)
		}
		data, _ := json.Marshal(names)
		fmt.Println(string(data))
	case len(args) > 1 && args[0] == "-c" && strings.Contains(args[1], "llama_supports_gpu_offload"):
		// llama.cpp logs its devices before the answer, or the import fails.
		switch os.Getenv("IMPRINT_FAKE_OFFLOAD") {
		case "import-error":
			fmt.Fprintln(os.Stderr, "OSError: vulkan-1.dll not found")
			return 1
		case "True":
			fmt.Println("ggml_vulkan: Found 1 Vulkan devices:")
			fmt.Println("True")
		default:
			fmt.Println("False")
		}
	}
	return 0
}

// fakeRequirementNames reads the distribution names a requirements file lists.
func fakeRequirementNames(file string) []string {
	data, _ := os.ReadFile(file)
	var names []string
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, "-") {
			continue
		}
		names = append(names, normalizeDistribution(strings.FieldsFunc(line, func(r rune) bool { return strings.ContainsRune("=<>~!;[ @", r) })[0]))
	}
	return names
}

func fakeVenv() map[string]bool {
	var names []string
	if data, err := os.ReadFile(os.Getenv("IMPRINT_FAKE_VENV")); err == nil {
		_ = json.Unmarshal(data, &names)
	}
	installed := map[string]bool{}
	for _, name := range names {
		installed[name] = true
	}
	return installed
}

func saveFakeVenv(installed map[string]bool) {
	path := os.Getenv("IMPRINT_FAKE_VENV")
	if path == "" {
		return
	}
	names := []string{}
	for name := range installed {
		names = append(names, name)
	}
	sort.Strings(names)
	data, _ := json.Marshal(names)
	_ = os.WriteFile(path, data, 0o644)
}

// fakeCalls reads back the calls fakeTool recorded.
func fakeCalls(t *testing.T, log string) []string {
	t.Helper()
	data, _ := os.ReadFile(log)
	return strings.Split(strings.TrimSpace(string(data)), "\n")
}
