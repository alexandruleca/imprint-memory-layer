package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
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
//	dry-run <requirements files>   uv pip install --dry-run
//	uninstall <distributions>      uv pip uninstall / pip uninstall
//	cache-clean <distribution>     uv cache clean
//
//	optional-tools uv=<uv> data=<dir>  python -m imprint.optional_tools
//
// IMPRINT_FAKE_UNUSED lists what Imprint no longer uses (a JSON list), and
// llama.cpp's offload answer is IMPRINT_FAKE_OFFLOAD.
// A dry run would upgrade the installed distributions IMPRINT_FAKE_OUTDATED
// lists (comma-separated).
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
	case len(args) > 0 && args[0] == "install" && slices.Contains(args, "--dry-run"):
		var files []string
		installed, outdated := fakeVenv(), strings.Split(os.Getenv("IMPRINT_FAKE_OUTDATED"), ",")
		adds, upgrades := 0, 0
		for i, arg := range args {
			if arg == "-r" {
				files = append(files, filepath.Base(args[i+1]))
				for _, name := range fakeRequirementNames(args[i+1]) {
					switch {
					case !installed[name]:
						adds++
					case slices.Contains(outdated, name):
						upgrades++
					}
				}
			}
		}
		record("dry-run " + strings.Join(files, " "))
		if upgrades > 0 {
			fmt.Printf("Would uninstall %d packages\n", upgrades)
		}
		if adds+upgrades == 0 {
			fmt.Println("Would make no changes")
		} else {
			fmt.Printf("Would install %d packages\n", adds+upgrades)
		}
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
	case len(args) > 2 && args[0] == "-m" && args[1] == "imprint.onnx_runtime" && args[2] == "unused":
		// What imprint.onnx_runtime would name: the installed distributions
		// IMPRINT_FAKE_UNUSED lists.
		var listed []string
		_ = json.Unmarshal([]byte(os.Getenv("IMPRINT_FAKE_UNUSED")), &listed)
		installed := fakeVenv()
		unused := []string{}
		for _, name := range listed {
			if installed[name] {
				unused = append(unused, name)
			}
		}
		data, _ := json.Marshal(unused)
		fmt.Println(string(data))
	case len(args) > 1 && args[0] == "-m" && args[1] == "imprint.optional_tools":
		// Setup passes its uv on; the answer is IMPRINT_FAKE_TOOLS.
		record("optional-tools uv=" + os.Getenv("IMPRINT_UV") + " data=" + filepath.Base(os.Getenv("IMPRINT_DATA_DIR")))
		fmt.Print(os.Getenv("IMPRINT_FAKE_TOOLS"))
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
