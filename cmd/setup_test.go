package cmd

import (
	"os"
	"path/filepath"
	"reflect"
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

func TestCUDAMajors(t *testing.T) {
	header := "| NVIDIA-SMI 580.105.07             Driver Version: 581.80         CUDA Version: 13.0     |"
	if m := driverCUDARe.FindStringSubmatch(header); m == nil || m[1] != "13" {
		t.Errorf("driver CUDA from %q = %v, want 13", header, m)
	}
	if got := lockedCUDAMajor(filepath.Join("..", "requirements", "gpu.lock.txt")); got != 13 {
		t.Errorf("lockedCUDAMajor(gpu.lock.txt) = %d, want 13", got)
	}
	unlocked := filepath.Join(t.TempDir(), "gpu.txt")
	if err := os.WriteFile(unlocked, []byte("onnxruntime-gpu[cuda,cudnn]>=1.20.0\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := lockedCUDAMajor(unlocked); got != 0 {
		t.Errorf("lockedCUDAMajor(unlocked) = %d, want 0", got)
	}
}
