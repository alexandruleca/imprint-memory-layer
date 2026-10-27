package cmd

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestInstallLlamaKeepsTheGPUBuildOnlyWhenItOffloads(t *testing.T) {
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
		{"cpu", "True", []string{"install llm.lock.txt"}},
		{"auto", "True", []string{"install llm-gpu.lock.txt"}},
		{"gpu", "False", []string{"install llm-gpu.lock.txt", "install llm.lock.txt"}},
		{"auto", "import-error", []string{"install llm-gpu.lock.txt", "install llm.lock.txt"}},
	}
	for _, c := range cases {
		log := filepath.Join(t.TempDir(), "installs")
		t.Setenv("IMPRINT_FAKE_TOOL_LOG", log)
		t.Setenv("IMPRINT_FAKE_OFFLOAD", c.offload)
		holdRuntime(project)
		installLlama("", os.Args[0], os.Args[0], project, c.profile)
		releaseRuntime()
		if got := fakeCalls(t, log); !reflect.DeepEqual(got, c.want) {
			t.Errorf("profile %s, offload %s: installed %v, want %v", c.profile, c.offload, got, c.want)
		}
	}
}
