package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"

	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/queuelock"
)

func TestStaleOrtProviders(t *testing.T) {
	cases := []struct {
		name      string
		providers map[string]bool
		want      []string
	}{
		{"fresh venv", map[string]bool{}, nil},
		{"intact", map[string]bool{"onnxruntime": true}, nil},
		{"half-deleted", map[string]bool{"onnxruntime": false}, []string{"onnxruntime"}},
		{"CUDA build from before ADR 0086", map[string]bool{"onnxruntime-gpu": true}, []string{"onnxruntime-gpu"}},
		// Removing the other build deletes shared files, so the CPU build is
		// reinstalled even though its own RECORD is still complete.
		{"both installed", map[string]bool{"onnxruntime": true, "onnxruntime-gpu": true}, []string{"onnxruntime", "onnxruntime-gpu"}},
		{"metadata spelling", map[string]bool{"onnxruntime_gpu": true}, []string{"onnxruntime_gpu"}},
	}
	for _, c := range cases {
		if got := staleOrtProviders(c.providers, cpuOrt); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: staleOrtProviders(%v) = %v, want %v", c.name, c.providers, got, c.want)
		}
	}
}

func TestRequirementInstallArgs(t *testing.T) {
	cases := []struct {
		files []string
		want  []string
	}{
		{[]string{"base.lock.txt", "cpu.lock.txt"}, []string{"-r", "base.lock.txt", "-r", "cpu.lock.txt", "--require-hashes"}},
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
		"llm.lock.txt":  "llama-cpp-python==0.3.36\nnumpy==2.5.4\n",
		// The GPU builds of ADR 0122.
		"onnx-cuda.lock.txt": "numpy==2.5.4\nnvidia-cublas==13.8.0.4\nonnxruntime-gpu==1.31.0\n",
		"onnx-rocm.lock.txt": "coloredlogs==15.0.1\n",
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

func TestResolveInstallProfile(t *testing.T) {
	cases := []struct {
		name   string
		flag   string
		env    string
		stored profileState
		want   profileState
	}{
		{"fresh install", "", "", profileState{}, profileState{Profile: "auto"}},
		// Installers before the chosen mark recorded their non-interactive
		// cpu default; setup tries the GPU for it.
		{"cpu the installer defaulted to", "", "", profileState{Profile: "cpu"}, profileState{Profile: "auto"}},
		{"cpu the user chose", "", "", profileState{Profile: "cpu", Chosen: true}, profileState{Profile: "cpu", Chosen: true}},
		{"flag over the stored choice", "GPU", "", profileState{Profile: "cpu", Chosen: true}, profileState{Profile: "gpu", Chosen: true}},
		{"env", "", "cpu", profileState{}, profileState{Profile: "cpu", Chosen: true}},
		{"unknown flag", "cuda", "", profileState{Profile: "cpu", Chosen: true}, profileState{Profile: "auto"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			SetInstallProfile(c.flag)
			t.Cleanup(func() { SetInstallProfile("") })
			t.Setenv("IMPRINT_PROFILE", c.env)
			if got := resolveInstallProfile(c.stored); got != c.want {
				t.Errorf("resolveInstallProfile(%+v) = %+v, want %+v", c.stored, got, c.want)
			}
		})
	}
}

func TestInstallCoreRuntime(t *testing.T) {
	cpuVenv := []string{"numpy", "onnxruntime", "sympy"}
	cases := []struct {
		name, outdated string
		venv, unused   []string
		wantVenv       []string
		wantCalls      []string
		// Whether setup freed the runtime: a change that replaces or removes
		// installed files needs it, adding packages does not (ADR 0084).
		wantFreed bool
	}{
		{
			// Embeddings ran on CUDA before ADR 0086. onnx, which only the
			// CUDA check used, is required by nothing that is removed.
			name: "CUDA venv from before", venv: []string{"numpy", "nvidia-cublas", "nvidia-curand", "onnx", "onnxruntime-gpu"},
			unused:   []string{"nvidia-cublas", "nvidia-curand", "onnxruntime-gpu"},
			wantVenv: []string{"numpy", "onnx", "onnxruntime", "sympy"},
			wantCalls: []string{"dry-run base.lock.txt cpu.lock.txt", "install base.lock.txt cpu.lock.txt", "uninstall onnxruntime onnxruntime-gpu",
				"dry-run base.lock.txt cpu.lock.txt", "install base.lock.txt cpu.lock.txt", "uninstall nvidia-cublas nvidia-curand",
				"cache-clean nvidia-cublas", "cache-clean nvidia-curand", "cache-clean onnxruntime-gpu"},
			wantFreed: true,
		},
		{
			name: "nothing to change", venv: cpuVenv, wantVenv: cpuVenv,
			wantCalls: []string{"dry-run base.lock.txt cpu.lock.txt"},
		},
		{
			name: "upgrade", outdated: "numpy", venv: cpuVenv, wantVenv: cpuVenv,
			wantCalls: []string{"dry-run base.lock.txt cpu.lock.txt", "install base.lock.txt cpu.lock.txt"}, wantFreed: true,
		},
		{
			name: "fresh venv", venv: []string{}, wantVenv: cpuVenv,
			wantCalls: []string{"dry-run base.lock.txt cpu.lock.txt", "install base.lock.txt cpu.lock.txt"},
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			project := runtimeProject(t)
			log, venv := filepath.Join(t.TempDir(), "calls"), filepath.Join(t.TempDir(), "venv.json")
			saveFakeVenvTo(t, venv, c.venv)
			unused, _ := json.Marshal(append([]string{}, c.unused...))
			t.Setenv("IMPRINT_FAKE_TOOL_LOG", log)
			t.Setenv("IMPRINT_FAKE_VENV", venv)
			t.Setenv("IMPRINT_FAKE_OUTDATED", c.outdated)
			t.Setenv("IMPRINT_FAKE_UNUSED", string(unused))
			fake := os.Args[0]
			holdRuntime(project)
			defer releaseRuntime()
			installCoreRuntime(fake, fake, fake, project, "auto")
			if got := fakeCalls(t, log); !reflect.DeepEqual(got, c.wantCalls) {
				t.Errorf("calls:\n  got  %q\n  want %q", got, c.wantCalls)
			}
			if venvChange.freed != c.wantFreed {
				t.Errorf("freed the runtime = %v, want %v", venvChange.freed, c.wantFreed)
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

const (
	cudaPlan = `[{"name":"cuda","distribution":"onnxruntime-gpu","requirements":"onnx-cuda","provider":"CUDAExecutionProvider","reason":"GPU 0: NVIDIA RTX"},` +
		`{"name":"cpu","distribution":"onnxruntime","requirements":"cpu","provider":"","reason":"the fallback"}]`
	rocmPlan = `[{"name":"rocm","distribution":"onnxruntime-migraphx","requirements":"onnx-rocm","provider":"MIGraphXExecutionProvider","reason":"ROCm 7.2.1",` +
		`"wheel":{"url":"https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2.1/onnxruntime_migraphx-1.23.2-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl","sha256":"663b"}},` +
		`{"name":"cpu","distribution":"onnxruntime","requirements":"cpu","provider":"","reason":"the fallback"}]`
)

// TestInstallCoreRuntimeGPUBuilds: a GPU build replaces the CPU build's
// onnxruntime when its check passes; otherwise the CPU build replaces it
// and what only it brought in (ADR 0122).
func TestInstallCoreRuntimeGPUBuilds(t *testing.T) {
	cuda := "base.lock.txt cpu.lock.txt onnx-cuda.lock.txt -onnxruntime"
	cases := []struct {
		name, plan, check, unused string
		venv, wantVenv            []string
		wantCalls                 []string
	}{
		{
			name: "fresh venv, CUDA runs", plan: cudaPlan, check: "ok", venv: []string{},
			wantVenv:  []string{"numpy", "nvidia-cublas", "onnxruntime-gpu", "sympy"},
			wantCalls: []string{"dry-run " + cuda, "install " + cuda, "check cuda"},
		},
		{
			name: "CPU venv, CUDA runs", plan: cudaPlan, check: "ok", venv: []string{"numpy", "onnxruntime", "sympy"},
			wantVenv: []string{"numpy", "nvidia-cublas", "onnxruntime-gpu", "sympy"},
			wantCalls: []string{"dry-run " + cuda, "install " + cuda, "uninstall onnxruntime onnxruntime-gpu",
				"dry-run " + cuda, "install " + cuda, "check cuda"},
		},
		{
			name: "CUDA does not run: back to the CPU build", plan: cudaPlan, check: "fail", venv: []string{},
			unused:   `{"cpu": ["nvidia-cublas", "onnxruntime-gpu"]}`,
			wantVenv: []string{"numpy", "onnxruntime", "sympy"},
			wantCalls: []string{"dry-run " + cuda, "install " + cuda, "check cuda",
				"dry-run base.lock.txt cpu.lock.txt", "install base.lock.txt cpu.lock.txt", "uninstall onnxruntime onnxruntime-gpu",
				"dry-run base.lock.txt cpu.lock.txt", "install base.lock.txt cpu.lock.txt", "uninstall nvidia-cublas",
				"cache-clean nvidia-cublas", "cache-clean onnxruntime-gpu"},
		},
		{
			name: "ROCm: AMD's pinned wheel", plan: rocmPlan, check: "ok", venv: []string{},
			wantVenv: []string{"coloredlogs", "numpy", "onnxruntime-migraphx", "sympy"},
			wantCalls: []string{"dry-run base.lock.txt cpu.lock.txt onnx-rocm.lock.txt onnx-rocm-wheel.lock.txt -onnxruntime",
				"install base.lock.txt cpu.lock.txt onnx-rocm.lock.txt onnx-rocm-wheel.lock.txt -onnxruntime", "check rocm"},
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			project := runtimeProject(t)
			log, venv := filepath.Join(t.TempDir(), "calls"), filepath.Join(t.TempDir(), "venv.json")
			saveFakeVenvTo(t, venv, c.venv)
			t.Setenv("IMPRINT_FAKE_TOOL_LOG", log)
			t.Setenv("IMPRINT_FAKE_VENV", venv)
			t.Setenv("IMPRINT_FAKE_OUTDATED", "")
			t.Setenv("IMPRINT_FAKE_UNUSED", c.unused)
			t.Setenv("IMPRINT_FAKE_PLAN", c.plan)
			t.Setenv("IMPRINT_FAKE_CHECK", c.check)
			fake := os.Args[0]
			holdRuntime(project)
			defer releaseRuntime()
			installCoreRuntime(fake, fake, fake, project, "auto")
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

// TestPlanOrtBuildsWithoutUvIsTheCPUBuild: only uv can leave out the CPU
// build faster-whisper requires.
func TestPlanOrtBuildsWithoutUvIsTheCPUBuild(t *testing.T) {
	t.Setenv("IMPRINT_FAKE_TOOL_LOG", filepath.Join(t.TempDir(), "calls"))
	t.Setenv("IMPRINT_FAKE_PLAN", cudaPlan)
	if got := planOrtBuilds("", os.Args[0], t.TempDir(), "auto"); len(got) != 1 || got[0].Name != "cpu" {
		t.Errorf("planOrtBuilds without uv = %+v", got)
	}
	if got := planOrtBuilds(os.Args[0], os.Args[0], t.TempDir(), "auto"); len(got) != 2 || got[0].Name != "cuda" {
		t.Errorf("planOrtBuilds = %+v", got)
	}
	t.Setenv("IMPRINT_FAKE_PLAN", `[{"name":"cuda"}]`) // Not ending with the CPU build.
	if got := planOrtBuilds(os.Args[0], os.Args[0], t.TempDir(), "auto"); len(got) != 1 || got[0].Name != "cpu" {
		t.Errorf("planOrtBuilds of a bad plan = %+v", got)
	}
}

func saveFakeVenvTo(t *testing.T, path string, names []string) {
	t.Helper()
	data, _ := json.Marshal(names)
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestSetupReportsItsStepsUntilItReleasesTheRuntime(t *testing.T) {
	project := runtimeProject(t)
	dataDir := platform.DataDir(project)
	holdRuntime(project)
	setupStep(2, 3, "installing llama.cpp")
	p, ok := queuelock.ReadProgress(dataDir)
	if !ok || p.String() != "setup: installing llama.cpp, 1/3 steps" {
		t.Fatalf("ReadProgress = %+v, %v", p, ok)
	}
	releaseRuntime()
	if _, ok := queuelock.ReadProgress(dataDir); ok {
		t.Error("the record outlived setup")
	}
}
