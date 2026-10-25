package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

func TestStaleOrtProviders(t *testing.T) {
	cases := []struct {
		name      string
		providers map[string]bool
		selected  string
		want      []string
	}{
		{"fresh venv", map[string]bool{}, "onnxruntime-gpu", nil},
		{"selected intact", map[string]bool{"onnxruntime-gpu": true}, "onnxruntime-gpu", nil},
		{"selected half-deleted", map[string]bool{"onnxruntime-gpu": false}, "onnxruntime-gpu", []string{"onnxruntime-gpu"}},
		{"switch build", map[string]bool{"onnxruntime": true}, "onnxruntime-gpu", []string{"onnxruntime"}},
		// Removing the other build deletes shared files, so the selected one
		// is reinstalled even though its own RECORD is still complete.
		{"both installed", map[string]bool{"onnxruntime": true, "onnxruntime-gpu": true}, "onnxruntime-gpu", []string{"onnxruntime", "onnxruntime-gpu"}},
		{"metadata spelling", map[string]bool{"onnxruntime_gpu": false}, "onnxruntime-gpu", []string{"onnxruntime_gpu"}},
	}
	for _, c := range cases {
		if got := staleOrtProviders(c.providers, c.selected); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: staleOrtProviders(%v, %q) = %v, want %v", c.name, c.providers, c.selected, got, c.want)
		}
	}
}

func TestRequirementInstallArgs(t *testing.T) {
	cases := []struct {
		files []string
		want  []string
	}{
		{[]string{"base.lock.txt", "gpu.lock.txt"}, []string{"-r", "base.lock.txt", "-r", "gpu.lock.txt", "--require-hashes"}},
		{[]string{"base.lock.txt", "cpu.txt"}, []string{"-r", "base.lock.txt", "-r", "cpu.txt"}},
		{[]string{"llm.txt"}, []string{"-r", "llm.txt"}},
	}
	for _, c := range cases {
		if got := requirementInstallArgs(c.files...); !reflect.DeepEqual(got, c.want) {
			t.Errorf("requirementInstallArgs(%v) = %v, want %v", c.files, got, c.want)
		}
	}
}

// runtimeProject is an install tree whose locks name a few distributions.
func runtimeProject(t *testing.T) string {
	t.Helper()
	project := t.TempDir()
	locks := map[string]string{
		"base.lock.txt": "numpy==2.5.4 \\\n    --hash=sha256:00\n    # via -r requirements/base.txt\n",
		"cpu.lock.txt":  "numpy==2.5.4\nonnxruntime==1.31.0 ; sys_platform != 'darwin'\nsympy==1.14.0\n",
		"gpu.lock.txt": "# via onnxruntime-gpu\nnumpy==2.5.4\nnvidia-cublas==13.8.0.4\nnvidia-curand==10.4.4.72 ; sys_platform == 'linux' \\\n" +
			"    --hash=sha256:00\nonnx==1.23.2\nonnxruntime-gpu==1.31.0\n",
		"llm.lock.txt": "llama-cpp-python==0.3.36\nnumpy==2.5.4\n",
		"gpu.txt":      "onnxruntime-gpu>=1.20.0\nnvidia-cuda-runtime~=13.0\n",
	}
	if err := os.MkdirAll(filepath.Join(project, "requirements"), 0o755); err != nil {
		t.Fatal(err)
	}
	for name, content := range locks {
		if err := os.WriteFile(filepath.Join(project, "requirements", name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return project
}

func TestProvisionEmbeddingRuntime(t *testing.T) {
	gpuVenv := []string{"numpy", "nvidia-cublas", "nvidia-curand", "onnx", "onnxruntime-gpu"}
	cpuVenv := []string{"numpy", "onnxruntime", "sympy"}
	cpuPlan := `{"requested": "cpu", "runtime": "cpu", "reason": "model.device is cpu"}`
	gpuPlan := `{"requested": "gpu", "runtime": "gpu", "reason": "NVIDIA GeForce RTX 4080 Laptop GPU, CUDA 13"}`
	cases := []struct {
		name, plan, check string
		retry             bool
		venv, wantVenv    []string
		wantCalls         []string
	}{
		{
			// An install from before ADR 0062: the GPU profile installed CUDA
			// while embeddings ran on the CPU.
			name: "cpu device on a GPU venv", plan: cpuPlan, venv: gpuVenv, wantVenv: cpuVenv,
			wantCalls: []string{"plan", "install base.lock.txt cpu.lock.txt", "uninstall onnxruntime onnxruntime-gpu",
				"install base.lock.txt cpu.lock.txt", "uninstall nvidia-cublas nvidia-curand onnx",
				"cache-clean nvidia-cublas", "cache-clean nvidia-curand", "cache-clean onnx", "cache-clean onnxruntime-gpu", "cache-prune"},
		},
		{
			name: "cpu device, nothing to change", plan: cpuPlan, venv: cpuVenv, wantVenv: cpuVenv,
			wantCalls: []string{"plan", "install base.lock.txt cpu.lock.txt", "cache-prune"},
		},
		{
			name: "fresh venv", plan: cpuPlan, venv: []string{}, wantVenv: cpuVenv,
			wantCalls: []string{"plan", "install base.lock.txt cpu.lock.txt", "cache-prune"},
		},
		{
			name: "gpu enabled and CUDA works", plan: gpuPlan, check: "ok", venv: cpuVenv, wantVenv: gpuVenv,
			wantCalls: []string{"plan", "install base.lock.txt gpu.lock.txt", "uninstall onnxruntime onnxruntime-gpu",
				"install base.lock.txt gpu.lock.txt", "uninstall sympy", "cache-clean onnxruntime", "cache-clean sympy", "check", "cache-prune"},
		},
		{
			name: "gpu enabled but CUDA fails", plan: gpuPlan, check: "broken", venv: cpuVenv, wantVenv: cpuVenv,
			wantCalls: []string{"plan", "install base.lock.txt gpu.lock.txt", "uninstall onnxruntime onnxruntime-gpu",
				"install base.lock.txt gpu.lock.txt", "uninstall sympy", "cache-clean onnxruntime", "cache-clean sympy", "check",
				"install base.lock.txt cpu.lock.txt", "uninstall onnxruntime onnxruntime-gpu",
				"install base.lock.txt cpu.lock.txt", "uninstall nvidia-cublas nvidia-curand onnx",
				"cache-clean nvidia-cublas", "cache-clean nvidia-curand", "cache-clean onnx", "cache-clean onnxruntime-gpu", "cache-prune"},
		},
		{
			name: "retry", plan: gpuPlan, check: "ok", retry: true, venv: gpuVenv, wantVenv: gpuVenv,
			wantCalls: []string{"plan --retry", "install base.lock.txt gpu.lock.txt", "check", "cache-prune"},
		},
		{
			name: "plan unavailable", plan: "Traceback (most recent call last):", venv: cpuVenv, wantVenv: cpuVenv,
			wantCalls: []string{"plan", "install base.lock.txt cpu.lock.txt", "cache-prune"},
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			project := runtimeProject(t)
			log, venv := filepath.Join(t.TempDir(), "calls"), filepath.Join(t.TempDir(), "venv.json")
			saveFakeVenvTo(t, venv, c.venv)
			t.Setenv("IMPRINT_FAKE_TOOL_LOG", log)
			t.Setenv("IMPRINT_FAKE_VENV", venv)
			t.Setenv("IMPRINT_FAKE_PLAN", c.plan)
			t.Setenv("IMPRINT_FAKE_CHECK", c.check)
			// The GPU lock's NVIDIA libraries and onnx are in no other file,
			// and sympy is only in the CPU lock.
			t.Setenv("IMPRINT_FAKE_UNUSED", `{"cpu": ["nvidia-cublas", "nvidia-curand", "onnx", "onnxruntime-gpu"], "gpu": ["onnxruntime", "sympy"]}`)
			fake := os.Args[0]
			provisionEmbeddingRuntime(fake, fake, fake, project, t.TempDir(), c.retry)
			if got := fakeCalls(t, log); !reflect.DeepEqual(got, c.wantCalls) {
				t.Errorf("calls:\n  got  %q\n  want %q", got, c.wantCalls)
			}
			var got []string
			for name := range fakeVenv() {
				got = append(got, name)
			}
			sort.Strings(got)
			if !reflect.DeepEqual(got, c.wantVenv) {
				t.Errorf("venv = %v, want %v", got, c.wantVenv)
			}
		})
	}
}

func saveFakeVenvTo(t *testing.T, path string, names []string) {
	t.Helper()
	data, _ := json.Marshal(names)
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
}
