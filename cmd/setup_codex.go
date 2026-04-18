package cmd

import (
	"os"
	"path/filepath"

	"github.com/hunter/imprint/internal/hooks"
	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
	"github.com/hunter/imprint/internal/tomlutil"
)

// SetupCodex wires Imprint into the OpenAI Codex CLI. Codex (April 2026)
// supports: per-user MCP registry via ~/.codex/config.toml, AGENTS.md for
// always-loaded rules, and hooks via ~/.codex/hooks.json gated behind the
// `features.codex_hooks` flag.
//
// Parity with Claude Code:
//   - MCP registration: full parity via `[mcp_servers.imprint]`.
//   - Rules: AGENTS.md managed block via markers.
//   - SessionStart: wired, same payload shape as Claude Code.
//   - Stop: wired, includes transcript_path — session summarizer works unchanged.
//   - PreToolUse / PostToolUse: Codex only fires these on Bash, so our
//     Read|Grep gate and search sentinel aren't reachable. Skipped here —
//     enforcement on Codex is text-only via the AGENTS.md rule.
//   - PreCompact: Codex has no equivalent event; skipped.
func SetupCodex() {
	codexPath := platform.CodexConfigPath()
	codexDir := filepath.Dir(codexPath)
	if !platform.DirExists(codexDir) {
		output.Warn("Codex config dir not found: " + codexDir + " — install the Codex CLI first (https://developers.openai.com/codex). Skipping.")
		return
	}
	output.Success("Codex config dir found: " + codexDir)
	setupHostsRan++

	bp := setupBackend()

	// Step 1: MCP server registration.
	output.Info("Checking Codex MCP registration...")
	spec := map[string]any{
		"command": bp.VenvPython,
		"args":    []any{"-m", "imprint"},
		"env": map[string]any{
			"PYTHONPATH":       bp.ProjectDir,
			"IMPRINT_DATA_DIR": bp.DataDir,
		},
	}
	added, err := tomlutil.EnsureMCPServer(codexPath, "imprint", spec)
	if err != nil {
		output.Warn("Could not update " + codexPath + ": " + err.Error())
	} else if added {
		output.Success("Registered imprint MCP server in " + codexPath)
	} else {
		output.Skip("imprint MCP server already registered in " + codexPath)
	}

	// Step 2: write the managed rule block into ~/.codex/AGENTS.md.
	agentsPath := platform.CodexAgentsPath()
	output.Info("Checking Codex AGENTS.md...")
	existing := ""
	if data, readErr := os.ReadFile(agentsPath); readErr == nil {
		existing = string(data)
	}
	managed := instructions.MarkerStart + "\n" + instructions.CodexRule + instructions.MarkerEnd + "\n"
	updated := instructions.MergeManaged(existing, managed)
	if updated == existing {
		output.Skip("Codex AGENTS.md already up to date at " + agentsPath)
	} else {
		if err := os.MkdirAll(filepath.Dir(agentsPath), 0755); err != nil {
			output.Warn("Could not create " + filepath.Dir(agentsPath) + ": " + err.Error())
		} else if err := os.WriteFile(agentsPath, []byte(updated), 0644); err != nil {
			output.Warn("Could not write " + agentsPath + ": " + err.Error())
		} else {
			output.Success("Wrote Codex AGENTS.md to " + agentsPath)
		}
	}

	// Step 3: flip the codex_hooks feature flag in config.toml so the hooks
	// file is actually consulted.
	flagChanged, err := tomlutil.SetBoolInSection(codexPath, "features", "codex_hooks", true)
	if err != nil {
		output.Warn("Could not toggle features.codex_hooks: " + err.Error())
	} else if flagChanged {
		output.Success("Enabled features.codex_hooks in " + codexPath)
	} else {
		output.Skip("features.codex_hooks already enabled")
	}

	// Step 4: hooks.json. Only SessionStart + Stop apply cleanly to Codex.
	// PreToolUse/PostToolUse fire on Bash only, so our Read|Grep gate and
	// MCP-search sentinel can't be wired here — rely on AGENTS.md text rule.
	hooksPath := platform.CodexHooksPath()
	hp := hooks.Paths{ProjectDir: bp.ProjectDir, VenvPython: bp.VenvPython, DataDir: bp.DataDir}
	type codexHook struct {
		event, matcher, command string
		timeout                 int
		async                   bool
	}
	codexHooks := []codexHook{
		{"SessionStart", "startup|resume", hooks.SessionStartCommand(), 10, false},
		{"Stop", "", hooks.StopCommand(hp), 120, true},
	}
	for _, h := range codexHooks {
		if err := jsonutil.SetHookWithMatcher(hooksPath, h.event, h.matcher, h.command, h.timeout, h.async); err != nil {
			output.Warn("Could not set " + h.event + " hook: " + err.Error())
		} else {
			label := h.event
			if h.matcher != "" {
				label += "(" + h.matcher + ")"
			}
			output.Success("Configured " + label + " hook")
		}
	}

	output.Header("═══ Imprint → Codex setup complete ═══")
	venvPythonVer, _ := runner.RunCapture(bp.VenvPython, "--version")
	if venvPythonVer != "" {
		output.Info("Python:     " + venvPythonVer + " (" + bp.VenvPython + ")")
	}
	output.Info("Data:       " + bp.DataDir)
	output.Info("MCP config: " + codexPath)
	output.Info("Rule:       " + agentsPath)
	output.Info("Hooks:      " + hooksPath)
	output.Warn("Codex hooks only fire on Bash for PreToolUse/PostToolUse — Read/Grep gating is advisory via AGENTS.md only.")
	output.Info("Next steps:")
	output.Info("  1. Restart any running codex session to pick up the new MCP server + hooks")
	output.Info("  2. In codex, verify the 'imprint' MCP server is listed")
	output.Info("  3. Use 'imprint ingest <dir>' to index your project directories")
}
