package cmd

import (
	"flag"
	"fmt"
	"os"
	"strings"
)

// Bootstrap is the thin entry point installer scripts invoke. It parses
// `--profile` and `--non-interactive` flags, records them on the
// package-level install state, and then runs setupBackend() — the same code
// path `imprint setup` uses — without doing any MCP/Claude Code
// registration. Installers follow up with `imprint setup <target>` if they
// need the host-AI-tool wiring.
//
// Usage:
//
//	imprint bootstrap [--profile cpu|gpu|auto] [--non-interactive]
//
// Env alternative (set by the one-liner before exec'ing imprint):
//
//	IMPRINT_PROFILE={cpu|gpu|auto}
func Bootstrap(args []string) {
	fs := flag.NewFlagSet("bootstrap", flag.ExitOnError)
	profile := fs.String("profile", "", "llama.cpp build: cpu | gpu | auto (default: auto, the GPU build when llama.cpp finds a GPU)")
	nonInteractive := fs.Bool("non-interactive", false, "never prompt; fail fast if a choice is required")
	fs.Parse(args)

	if *profile != "" {
		SetInstallProfile(strings.ToLower(*profile))
	}
	SetNonInteractive(*nonInteractive)

	fmt.Fprintln(os.Stderr, "→ imprint bootstrap: provisioning venv + selected profile…")
	_ = setupBackend()
	fmt.Fprintln(os.Stderr, "→ imprint bootstrap: done. Run `imprint setup` to wire the MCP server into your AI tools.")
}
