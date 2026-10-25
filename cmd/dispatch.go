package cmd

import (
	"fmt"
	"slices"
	"strings"

	"github.com/hunter/imprint/internal/output"
)

// client is one AI tool Imprint connects to. The clients table is the one
// list of them: setup, enable, connect, disable, disconnect and imprint wsl
// resolve their target through it, their usage text names its entries, and
// `imprint clients` reports it, which is where the dashboard gets its choices.
type client struct {
	name    string
	label   string
	aliases []string
	// connect registers backend's runtime with the client. It returns
	// without changing anything when the client is not installed.
	connect func(backend func() backendPaths)
	// registrations are the files connect writes; disconnect cleans them up.
	registrations func() []clientCleanup
	// wslHome is the client's directory in a WSL profile, for the clients a
	// WSL profile can be registered for (imprint wsl setup). Claude Code and
	// Codex run inside WSL and get the bridge launcher, MCP entry, rules and
	// hooks. Cursor's agent runs from the Windows app over Remote-WSL, which
	// Imprint's Windows Cursor setup already wires; its WSL profile only holds
	// the conversations it records, so registering it writes nothing and lets
	// learn read them (ADR 0052).
	wslHome func(wslProfile) string
}

var clients = []client{
	{name: "claude-code", label: "Claude Code", aliases: []string{"claude"}, connect: SetupClaudeCode, registrations: claudeCodeRegistrations,
		wslHome: func(p wslProfile) string { return p.ClaudeHome }},
	{name: "claude-desktop", label: "Claude Desktop", connect: SetupClaudeDesktop, registrations: claudeDesktopRegistrations},
	{name: "chatgpt-desktop", label: "ChatGPT Desktop", aliases: []string{"chatgpt"}, connect: SetupChatGPTDesktop, registrations: chatGPTDesktopRegistrations},
	{name: "cursor", label: "Cursor", connect: SetupCursor, registrations: cursorRegistrations,
		wslHome: func(p wslProfile) string { return p.CursorHome }},
	{name: "codex", label: "Codex", connect: SetupCodex, registrations: codexRegistrations,
		wslHome: func(p wslProfile) string { return p.CodexHome }},
	{name: "copilot", label: "GitHub Copilot", connect: SetupCopilot, registrations: copilotRegistrations},
	{name: "cline", label: "Cline", connect: SetupCline, registrations: clineRegistrations},
	{name: "openclaw", label: "OpenClaw", aliases: []string{"clawdbot", "moltbot"}, connect: SetupOpenClaw, registrations: openClawRegistrations},
}

// selectClients resolves a target: one client by name or alias, or every
// client for "all". ok is false for an unknown target.
func selectClients(target string) (selected []client, ok bool) {
	if target == "all" {
		return clients, true
	}
	for _, c := range clients {
		if c.name == target || slices.Contains(c.aliases, target) {
			return []client{c}, true
		}
	}
	return nil, false
}

// ClientTargets is the usage text of a client target argument.
func ClientTargets() string {
	names := make([]string, 0, len(clients)+1)
	for _, c := range clients {
		names = append(names, c.name)
	}
	return strings.Join(append(names, "all"), " | ")
}

// wslTargets are the clients a WSL profile can be registered for.
func wslTargets() []string {
	var names []string
	for _, c := range clients {
		if c.wslHome != nil {
			names = append(names, c.name)
		}
	}
	return names
}

// WSLTargets is the usage text of a WSL target argument.
func WSLTargets() string {
	return strings.Join(wslTargets(), "|")
}

// wslTargetChoices names wslTargets for messages: "a, b or c".
func wslTargetChoices() string {
	names := wslTargets()
	return strings.Join(names[:len(names)-1], ", ") + " or " + names[len(names)-1]
}

// wslClientDir is a registered target's profile directory.
func wslClientDir(p wslProfile, target string) (string, error) {
	for _, c := range clients {
		if c.name == target && c.wslHome != nil {
			return c.wslHome(p), nil
		}
	}
	return "", fmt.Errorf("target must be %s", wslTargetChoices())
}

// setupHostsRan counts the clients whose handler found them installed and
// went on to connect them, so "all" can report that it found none.
var setupHostsRan int

// connectClients registers the runtime backend returns with the target's
// clients. Each handler skips its client when it is not installed. It
// returns false for an unknown target.
func connectClients(target string, backend func() backendPaths) bool {
	selected, ok := selectClients(target)
	if !ok {
		return false
	}
	if target != "all" {
		selected[0].connect(backend)
		return true
	}
	setupHostsRan = 0
	for _, c := range selected {
		output.Header("─── " + c.name + " ───")
		c.connect(backend)
	}
	if setupHostsRan == 0 {
		output.Warn("No supported clients found — install Claude Code, Claude Desktop, Cursor, Codex, Copilot, Cline, or OpenClaw, then run this again.")
	}
	return true
}

// Setup provisions the runtime (venv and dependencies) and connects the
// target's clients to it. It returns false for an unknown target.
func Setup(target string) bool {
	return connectClients(target, setupBackend)
}
