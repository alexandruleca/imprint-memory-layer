// Package gpustate persists sticky GPU-health state across imprint setup
// runs. Lives in data/gpu_state.json (preserved by install.sh rsync) so the
// same box doesn't retry the same doomed ORT/llama-cpp CUDA install on
// every `imprint setup`.
package gpustate

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"time"
)

const fileName = "gpu_state.json"

// State is the full on-disk shape. Absent fields mean "unknown / never tried".
type State struct {
	OrtGPU          string          `json:"ort_gpu,omitempty"`        // "ok" | "broken"
	OrtGPUReason    string          `json:"ort_gpu_reason,omitempty"` // first error line
	OrtGPUEnv       Env             `json:"ort_gpu_env,omitempty"`
	LlamaCudaFailed *LlamaCudaFail  `json:"llama_cuda_failed,omitempty"`
}

// Env fingerprints the host so we only trust a sticky verdict when the
// environment that produced it is still the same. Python is included so a
// verdict from a 3.12 venv doesn't outlive a 3.13 upgrade (wheel availability
// changes per minor version — e.g. onnxruntime-gpu 1.24.x stubs on cp313).
type Env struct {
	GPU        string `json:"gpu,omitempty"`
	Driver     string `json:"driver,omitempty"`
	Nvcc       string `json:"nvcc,omitempty"`
	ComputeCap string `json:"compute_cap,omitempty"`
	Python     string `json:"python,omitempty"`
}

// LlamaCudaFail records a failed llama-cpp CUDA rebuild.
type LlamaCudaFail struct {
	Env Env    `json:"env"`
	TS  string `json:"ts"`
}

// Path returns the on-disk location for the given data dir.
func Path(dataDir string) string {
	return filepath.Join(dataDir, fileName)
}

// Load reads state from dataDir/gpu_state.json. Missing file → empty state
// (not an error). Malformed file → zero state so setup can recover.
func Load(dataDir string) State {
	var s State
	data, err := os.ReadFile(Path(dataDir))
	if err != nil {
		return s
	}
	_ = json.Unmarshal(data, &s)
	return s
}

// Save writes state atomically (tmp + rename). Creates dataDir if missing.
func Save(dataDir string, s State) error {
	if err := os.MkdirAll(dataDir, 0755); err != nil {
		return err
	}
	path := Path(dataDir)
	content, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	content = append(content, '\n')
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, content, 0644); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return err
	}
	return nil
}

// Clear deletes the state file. Used by `imprint setup --retry-gpu`.
func Clear(dataDir string) error {
	err := os.Remove(Path(dataDir))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

// Now returns the current time in RFC3339 for embedding in sentinels.
func Now() string {
	return time.Now().UTC().Format(time.RFC3339)
}

// SameEnv reports whether two Envs describe the same host. Empty fields on
// either side are treated as wildcards — we only want to consider a sentinel
// valid when every field we did record still matches.
func SameEnv(a, b Env) bool {
	if a.GPU != "" && b.GPU != "" && a.GPU != b.GPU {
		return false
	}
	if a.Driver != "" && b.Driver != "" && a.Driver != b.Driver {
		return false
	}
	if a.Nvcc != "" && b.Nvcc != "" && a.Nvcc != b.Nvcc {
		return false
	}
	if a.ComputeCap != "" && b.ComputeCap != "" && a.ComputeCap != b.ComputeCap {
		return false
	}
	if a.Python != "" && b.Python != "" && a.Python != b.Python {
		return false
	}
	return true
}
