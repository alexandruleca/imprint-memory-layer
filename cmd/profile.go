package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Profile manages the install profile post-setup. The profile selects the
// local LLM's build: cpu, or the GPU build when llama.cpp finds a GPU (gpu
// and auto); the embedding runtime follows model.device (ADR 0062). Users
// can swap it, add or drop the local LLM tagger, or print the current
// state. Each mutation re-runs `setupBackend()` with the new flags so the
// venv actually reflects the persisted choice.
//
// Usage:
//
//	imprint profile                → print active profile
//	imprint profile show           → same as above
//	imprint profile set cpu|gpu    → change profile, reinstall deps
//	imprint profile add-llm        → install llama-cpp-python
//	imprint profile drop-llm       → uninstall llama-cpp-python
func Profile(args []string) {
	if len(args) == 0 || args[0] == "show" {
		printProfile()
		return
	}

	switch args[0] {
	case "set":
		if len(args) < 2 {
			output.Fail("usage: imprint profile set <cpu|gpu|auto>")
		}
		target := strings.ToLower(args[1])
		if target != "cpu" && target != "gpu" && target != "auto" {
			output.Fail("unknown profile '" + args[1] + "' — expected cpu, gpu, or auto")
		}
		SetInstallProfile(target)
		fmt.Fprintf(os.Stderr, "→ switching to profile=%s\n", target)
		_ = setupBackend()
	case "add-llm":
		addLLM()
	case "drop-llm":
		SetWithLLM(false)
		fmt.Fprintln(os.Stderr, "→ dropping llama-cpp-python from active profile")
		projectDir := platform.FindProjectDir()
		venvPython := platform.VenvPython(projectDir)
		if platform.FileExists(venvPython) {
			holdRuntime(projectDir)
			_ = pipUninstall(uvBinary(projectDir), venvPython, platform.VenvBin(projectDir, "pip"), "llama-cpp-python")
			releaseRuntime()
		}
		_ = setupBackend() // re-run to persist profile.json with with_llm=false
	default:
		output.Fail("unknown `profile` subcommand: " + args[0])
	}
}

// addLLM installs only the local LLM overlay into the existing environment
// and records it in profile.json. The dashboard runs this command on the
// first local chat (ADR 0035): adding packages leaves it running, while
// re-running setupBackend could also change the ONNX Runtime, which restarts
// the dashboard (ADR 0084).
func addLLM() {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	if !platform.FileExists(venvPython) {
		output.Fail("No Imprint environment at " + platform.VenvDir(projectDir) + " — run `imprint setup` first")
	}
	scopeUv(projectDir)
	holdRuntime(projectDir)
	defer releaseRuntime()
	stored := loadProfile(projectDir)
	installLLMOverlay(uvBinary(projectDir), venvPython, platform.VenvBin(projectDir, "pip"),
		projectDir, resolveInstallProfile(stored))
	if out, err := runner.RunCapture(venvPython, "-c", "import llama_cpp"); err != nil {
		output.Fail("llama-cpp-python does not import after installing:\n" + lastNonBlankLines(out, 3))
	}
	stored.WithLLM = true
	saveProfile(projectDir, stored)
	output.Success("Local LLM runtime installed")
}

func printProfile() {
	projectDir := platform.FindProjectDir()
	p := platform.ProfileStatePath(projectDir)
	data, err := os.ReadFile(p)
	if err != nil {
		fmt.Println("No profile recorded yet — run `imprint setup --profile <cpu|gpu>` or `imprint bootstrap` first.")
		return
	}
	var st struct {
		Profile string `json:"profile"`
		WithLLM bool   `json:"with_llm"`
	}
	if err := json.Unmarshal(data, &st); err != nil {
		output.Fail("profile.json is corrupt: " + err.Error())
	}
	fmt.Printf("Profile: %s (local LLM build; embeddings follow model.device)\n", st.Profile)
	fmt.Printf("Local LLM tagger: %v\n", st.WithLLM)
	fmt.Printf("State file: %s\n", p)
	fmt.Printf("Venv:  %s\n", filepath.Dir(filepath.Dir(platform.VenvPython(projectDir))))
}
