package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// TestMain lets the test binary stand in for the venv's pip and python:
// those receive flags the testing package would otherwise reject.
func TestMain(m *testing.M) {
	if log := os.Getenv("IMPRINT_FAKE_TOOL_LOG"); log != "" {
		os.Exit(fakeTool(log, os.Args[1:]))
	}
	os.Exit(m.Run())
}

func fakeTool(log string, args []string) int {
	if len(args) > 0 && args[0] == "install" {
		f, err := os.OpenFile(log, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			return 1
		}
		defer f.Close()
		for i, arg := range args[:len(args)-1] {
			if arg == "-r" {
				fmt.Fprintln(f, filepath.Base(args[i+1]))
			}
		}
		return 0
	}
	// python -c "...llama_supports_gpu_offload()": llama.cpp logs its
	// devices before the answer, or the import fails.
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
	return 0
}

func TestInstallLLMOverlayKeepsTheGPUBuildOnlyWhenItOffloads(t *testing.T) {
	project := t.TempDir()
	if err := os.MkdirAll(filepath.Join(project, "requirements"), 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"llm.lock.txt", "llm-gpu.lock.txt"} {
		if err := os.WriteFile(filepath.Join(project, "requirements", name), []byte("locked\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	cases := []struct {
		profile, offload string
		want             []string
	}{
		{"cpu", "True", []string{"llm.lock.txt"}},
		{"auto", "True", []string{"llm-gpu.lock.txt"}},
		{"gpu", "False", []string{"llm-gpu.lock.txt", "llm.lock.txt"}},
		{"auto", "import-error", []string{"llm-gpu.lock.txt", "llm.lock.txt"}},
	}
	for _, c := range cases {
		log := filepath.Join(t.TempDir(), "installs")
		t.Setenv("IMPRINT_FAKE_TOOL_LOG", log)
		t.Setenv("IMPRINT_FAKE_OFFLOAD", c.offload)
		installLLMOverlay("", os.Args[0], os.Args[0], project, c.profile)
		data, _ := os.ReadFile(log)
		if got := strings.Fields(string(data)); !reflect.DeepEqual(got, c.want) {
			t.Errorf("profile %s, offload %s: installed %v, want %v", c.profile, c.offload, got, c.want)
		}
	}
}
