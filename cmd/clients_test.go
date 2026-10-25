package cmd

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/hunter/imprint/internal/tomlutil"
)

func TestClientTargetsResolveNamesAliasesAndAll(t *testing.T) {
	all, ok := selectClients("all")
	if !ok || len(all) != len(clients) {
		t.Fatalf("all selected %d of %d clients", len(all), len(clients))
	}
	for target, name := range map[string]string{"claude-code": "claude-code", "claude": "claude-code", "chatgpt": "chatgpt-desktop", "moltbot": "openclaw", "cursor": "cursor"} {
		got, ok := selectClients(target)
		if !ok || len(got) != 1 || got[0].name != name {
			t.Fatalf("%s resolved to %v", target, got)
		}
	}
	if _, ok := selectClients("vim"); ok {
		t.Fatal("unknown target accepted")
	}
	usage := ClientTargets()
	for _, c := range clients {
		if !strings.Contains(usage, c.name+" | ") {
			t.Fatalf("usage %q misses %s", usage, c.name)
		}
	}
	if !strings.HasSuffix(usage, " | all") {
		t.Fatalf("usage %q misses all", usage)
	}
}

func TestWSLTargetsAreTheClientsWithAWSLHome(t *testing.T) {
	if got := wslTargets(); !slices.Equal(got, []string{"claude-code", "cursor", "codex", "copilot", "cline"}) {
		t.Fatalf("wsl targets %v", got)
	}
	if got := wslTargetChoices(); got != "claude-code, cursor, codex, copilot or cline" {
		t.Fatalf("choices %q", got)
	}
	p := wslProfile{ClaudeHome: "/h/.claude", CodexHome: "/h/.codex", CursorHome: "/h/.cursor", ClineHome: "/h/.cline", CopilotHome: "/h/.copilot"}
	for target, want := range map[string]string{"claude-code": p.ClaudeHome, "codex": p.CodexHome, "cursor": p.CursorHome, "cline": p.ClineHome, "copilot": p.CopilotHome} {
		if dir, err := wslClientDir(p, target); err != nil || dir != want {
			t.Fatalf("%s: %q, %v", target, dir, err)
		}
	}
	// Learn reads each target's store; Cline's sessions are in its data directory.
	for target, want := range map[string]string{"claude-code": p.ClaudeHome, "codex": p.CodexHome, "cursor": p.CursorHome, "cline": "/h/.cline/data/sessions", "copilot": p.CopilotHome} {
		if dir, err := wslStoreDir(p, target); err != nil || dir != want {
			t.Fatalf("%s store: %q, %v", target, dir, err)
		}
	}
	if dir, err := wslStoreDir(wslProfile{Home: "/h"}, "cline"); err != nil || dir != "" {
		t.Fatalf("a profile without Cline has a Cline store: %q, %v", dir, err)
	}
	if _, err := wslClientDir(p, "vim"); err == nil || !strings.Contains(err.Error(), "claude-code, cursor, codex, copilot or cline") {
		t.Fatalf("vim accepted: %v", err)
	}
}

func TestConnectClientsRunsOnlyTheTargetsHandlersWithTheGivenRuntime(t *testing.T) {
	saved := clients
	t.Cleanup(func() { clients = saved })
	runtime := backendPaths{ProjectDir: "/runtime", VenvPython: "/runtime/python", DataDir: "/runtime/data"}
	var connected []string
	fake := func(name string) client {
		return client{name: name, label: name, connect: func(backend func() backendPaths) {
			setupHostsRan++
			if backend() != runtime {
				t.Fatalf("%s connected another runtime", name)
			}
			connected = append(connected, name)
		}}
	}
	clients = []client{fake("a"), fake("b")}
	backend := func() backendPaths { return runtime }
	if !connectClients("b", backend) || !slices.Equal(connected, []string{"b"}) {
		t.Fatalf("one target connected %v", connected)
	}
	connected = nil
	if !connectClients("all", backend) || !slices.Equal(connected, []string{"a", "b"}) {
		t.Fatalf("all connected %v", connected)
	}
	if connectClients("c", backend) {
		t.Fatal("unknown target accepted")
	}
}

func TestClientsSharingARegistryListItOnce(t *testing.T) {
	shared := clientCleanup{registry: "/profile/.codex/config.toml"}
	own := clientCleanup{registry: "/profile/.cursor/mcp.json"}
	selected := []client{
		{name: "a", registrations: func() []clientCleanup { return []clientCleanup{shared} }},
		{name: "b", registrations: func() []clientCleanup { return []clientCleanup{shared, own} }},
	}
	got := registrationsOf(selected)
	if len(got) != 2 || got[0].registry != shared.registry || got[1].registry != own.registry {
		t.Fatalf("registrations %v", got)
	}
}

// Connect registers one client with the given runtime and disconnect
// removes only that client's registrations, in a disposable profile.
func TestConnectAndDisconnectOneClientLeaveTheOthersAlone(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("CODEX_HOME", "")
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	project := filepath.Join(home, "runtime")
	runtime := backendPaths{ProjectDir: project, VenvPython: filepath.Join(project, ".venv", "bin", "python"), DataDir: filepath.Join(project, "data")}
	if err := os.MkdirAll(filepath.Join(home, ".cursor"), 0o755); err != nil {
		t.Fatal(err)
	}
	codexConfig := filepath.Join(home, ".codex", "config.toml")
	writeCleanupFixture(t, codexConfig, "model = 'keep-model'\n")
	if _, err := tomlutil.EnsureMCPServer(codexConfig, "imprint", map[string]any{"command": runtime.VenvPython, "env": map[string]any{"PYTHONPATH": project}}); err != nil {
		t.Fatal(err)
	}
	cursor, _ := selectClients("cursor")
	codex, _ := selectClients("codex")
	connected := func(c []client) bool {
		t.Helper()
		ok, err := isConnected(c[0], project)
		if err != nil {
			t.Fatal(err)
		}
		return ok
	}
	if connected(cursor) || !connected(codex) {
		t.Fatal("fixture state wrong")
	}

	if !connectClients("cursor", func() backendPaths { return runtime }) {
		t.Fatal("cursor target refused")
	}
	if !connected(cursor) || !connected(codex) {
		t.Fatal("connecting cursor did not connect only cursor")
	}
	rule := filepath.Join(home, ".cursor", "rules", "imprint.mdc")
	if _, err := os.Stat(rule); err != nil {
		t.Fatal("cursor rule not written")
	}

	if err := disconnectClients(cursor, project); err != nil {
		t.Fatal(err)
	}
	if connected(cursor) || !connected(codex) {
		t.Fatal("disconnecting cursor did not disconnect only cursor")
	}
	if _, err := os.Stat(rule); !os.IsNotExist(err) {
		t.Fatal("cursor rule remains")
	}
	hooks, _ := os.ReadFile(filepath.Join(home, ".cursor", "hooks.json"))
	if strings.Contains(string(hooks), "hook_entry") {
		t.Fatalf("cursor hooks remain: %s", hooks)
	}

	if err := disconnectClients(codex, project); err != nil {
		t.Fatal(err)
	}
	if connected(codex) {
		t.Fatal("disconnecting codex left it connected")
	}
	content, _ := os.ReadFile(codexConfig)
	if !strings.Contains(string(content), "keep-model") {
		t.Fatalf("unrelated codex settings lost: %s", content)
	}
}

// Disconnecting one client removes only its WSL bridges, and repair keeps
// re-registering the other clients' installer selections.
func TestDisconnectingOneClientKeepsOtherWSLSelections(t *testing.T) {
	root := t.TempDir()
	project := filepath.Join(root, "runtime")
	writeCleanupFixture(t, filepath.Join(project, ".wsl-integrations.txt"), "Ubuntu|codex\nUbuntu|claude-code\nUbuntu|cursor\n")
	old := runWSL
	t.Cleanup(func() { runWSL = old })
	runWSL = func(args ...string) (string, error) {
		if strings.Contains(strings.Join(args, " "), "imprint-probe") {
			return "/home/me\n/home/me/.claude\n/home/me/.codex\n/home/me/.claude.json\n/home/me/.cursor\n\n\n", nil
		}
		return filepath.Join(root, "profiles", filepath.FromSlash(strings.TrimPrefix(args[len(args)-1], "/"))), nil
	}
	if err := cleanupWSLRegistrations(project, []string{"claude-code"}); err != nil {
		t.Fatal(err)
	}
	selections, _ := os.ReadFile(filepath.Join(project, ".wsl-integrations.txt"))
	if string(selections) != "Ubuntu|codex\nUbuntu|cursor\n" {
		t.Fatalf("selections after disconnecting claude-code: %q", selections)
	}
	if err := cleanupWSLRegistrations(project, wslTargets()); err != nil {
		t.Fatal(err)
	}
	selections, _ = os.ReadFile(filepath.Join(project, ".wsl-integrations.txt"))
	if len(selections) != 0 {
		t.Fatalf("selections after disconnecting every client: %q", selections)
	}
}
