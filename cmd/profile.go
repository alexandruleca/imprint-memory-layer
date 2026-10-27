package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
)

// Profile manages the install profile post-setup. The profile selects
// llama.cpp's build, which runs embeddings and the local LLM: cpu, or the GPU
// build when llama.cpp finds a GPU (gpu and auto; ADR 0049, 0086). Users can
// swap it or print the current state. A change re-runs `setupBackend()` so
// the venv actually reflects the persisted choice.
//
// Usage:
//
//	imprint profile                → print active profile
//	imprint profile show           → same as above
//	imprint profile set cpu|gpu    → change profile, reinstall deps
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
	default:
		output.Fail("unknown `profile` subcommand: " + args[0])
	}
}

func printProfile() {
	projectDir := platform.FindProjectDir()
	p := platform.ProfileStatePath(projectDir)
	data, err := os.ReadFile(p)
	if err != nil {
		fmt.Println("No profile recorded yet — run `imprint setup --profile <cpu|gpu>` or `imprint bootstrap` first.")
		return
	}
	var st profileState
	if err := json.Unmarshal(data, &st); err != nil {
		output.Fail("profile.json is corrupt: " + err.Error())
	}
	fmt.Printf("Profile: %s (llama.cpp build for embeddings and the local LLM)\n", st.Profile)
	fmt.Printf("State file: %s\n", p)
	fmt.Printf("Venv:  %s\n", filepath.Dir(filepath.Dir(platform.VenvPython(projectDir))))
}
