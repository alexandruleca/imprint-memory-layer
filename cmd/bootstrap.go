package cmd

import (
	"flag"
	"fmt"
	"os"
	"strings"
)

// Bootstrap is the thin entry point installer scripts invoke. It parses
// `--profile`, `--with-llm`, and `--non-interactive` flags, records them on
// the package-level install state, and then runs setupBackend() — the same
// code path `imprint setup` uses — without doing any MCP/Claude Code
// registration. Installers follow up with `imprint setup <target>` if they
// need the host-AI-tool wiring.
//
// Usage:
//
//	imprint bootstrap [--profile cpu|gpu|auto] [--with-llm] [--non-interactive]
//
// Env alternatives (set by the one-liner before exec'ing imprint):
//
//	IMPRINT_PROFILE={cpu|gpu|auto}
//	IMPRINT_WITH_LLM=1
func Bootstrap(args []string) {
	fs := flag.NewFlagSet("bootstrap", flag.ExitOnError)
	profile := fs.String("profile", "", "install profile: cpu | gpu | auto (default: auto)")
	llm := fs.Bool("with-llm", false, "install llama-cpp-python (local tagger + chat)")
	noLLM := fs.Bool("no-llm", false, "explicitly skip llama-cpp-python (overrides persisted profile)")
	nonInteractive := fs.Bool("non-interactive", false, "never prompt; fail fast if a choice is required")
	retryGpu := fs.Bool("retry-gpu", false, "clear sticky GPU failure cache before running")
	fs.Parse(args)

	if *profile != "" {
		SetInstallProfile(strings.ToLower(*profile))
	}
	switch {
	case *llm:
		SetWithLLM(true)
	case *noLLM:
		SetWithLLM(false)
	}
	SetNonInteractive(*nonInteractive)
	if *retryGpu {
		SetRetryGPU(true)
	}

	fmt.Fprintln(os.Stderr, "→ imprint bootstrap: provisioning venv + selected profile…")
	_ = setupBackend()
	fmt.Fprintln(os.Stderr, "→ imprint bootstrap: done. Run `imprint setup` to wire the MCP server into your AI tools.")
}
