package cmd

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
)

func writeCleanupFixture(t *testing.T, file, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(file), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(file, []byte(content), 0600); err != nil {
		t.Fatal(err)
	}
}
func TestCleanupPreservesUnrelatedClientState(t *testing.T) {
	for _, kind := range []string{"claude", "codex", "cursor", "copilot", "openclaw"} {
		t.Run(kind, func(t *testing.T) {
			root := t.TempDir()
			project := filepath.Join(root, "runtime")
			c := clientCleanup{registry: filepath.Join(root, "config.json"), keys: []string{"mcpServers"}, hooks: filepath.Join(root, "hooks.json"), rules: filepath.Join(root, "rules.md"), permission: true}
			spec := map[string]any{"command": filepath.Join(project, "python"), "env": map[string]any{"PYTHONPATH": project}}
			switch kind {
			case "codex":
				c.toml = true
				c.keys = []string{"mcp_servers"}
				writeCleanupFixture(t, c.registry, "# keep comment\nmodel = 'keep-model'\n[mcp_servers.imprint]\ncommand = '"+filepath.ToSlash(project)+"/python'\n[mcp_servers.other]\ncommand = 'keep-other'\n")
			default:
				if kind == "copilot" {
					c.keys = []string{"servers"}
				}
				if kind == "openclaw" {
					c.keys = []string{"mcp", "servers"}
				}
				if _, err := jsonutil.EnsureMCPServerNested(c.registry, c.keys, "imprint", spec); err != nil {
					t.Fatal(err)
				}
				if _, err := jsonutil.EnsureMCPServerNested(c.registry, c.keys, "other", map[string]any{"command": "keep-other"}); err != nil {
					t.Fatal(err)
				}
			}
			if kind == "cursor" {
				c.rule = instructions.CursorRule
				writeCleanupFixture(t, c.rules, c.rule)
				writeCleanupFixture(t, c.hooks, `{"version":1,"hooks":{"sessionStart":[{"command":"python imprint/hook_entry.py session-start"},{"command":"keep-hook"},"keep-opaque"]},"keep":true}`)
			} else {
				writeCleanupFixture(t, c.rules, "Keep my guidance.\n"+instructions.MarkerStart+"\nImprint rule\n"+instructions.MarkerEnd+"\n")
				writeCleanupFixture(t, c.hooks, `{"permissions":{"allow":["mcp__imprint__*","keep-permission"]},"hooks":{"Stop":[{"hooks":[{"command":"python imprint/hook_entry.py stop"},{"command":"keep-hook"}]}]},"keep":true}`)
			}
			for i := 0; i < 2; i++ {
				if err := cleanupClient(c, project); err != nil {
					t.Fatal(err)
				}
			}
			content, _ := os.ReadFile(c.registry)
			if strings.Contains(string(content), "[mcp_servers.imprint]") || strings.Contains(string(content), `"imprint":`) || !strings.Contains(string(content), "keep-other") {
				t.Fatalf("bad registry cleanup: %s", content)
			}
			content, _ = os.ReadFile(c.hooks)
			if strings.Contains(string(content), "imprint") || !strings.Contains(string(content), "keep-hook") {
				t.Fatalf("bad hook cleanup: %s", content)
			}
			if kind == "cursor" {
				if _, err := os.Stat(c.rules); !os.IsNotExist(err) {
					t.Fatal("owned rule remains")
				}
			} else {
				content, _ = os.ReadFile(c.rules)
				if strings.Contains(string(content), instructions.MarkerStart) || !strings.Contains(string(content), "Keep my guidance.") {
					t.Fatal("lost user rules")
				}
			}
		})
	}
}

func TestCleanupRefusesMalformedAndPreservesForeignRuntime(t *testing.T) {
	for _, registry := range []string{`{"mcpServers":{"imprint":{"command":"/different/runtime/python"}}}`, `{"broken":`} {
		root := t.TempDir()
		c := clientCleanup{registry: filepath.Join(root, "config.json"), keys: []string{"mcpServers"}, rules: filepath.Join(root, "rules.md")}
		writeCleanupFixture(t, c.registry, registry)
		writeCleanupFixture(t, c.rules, "untouched")
		err := cleanupClient(c, "/this/runtime")
		if strings.Contains(registry, "broken") && err == nil {
			t.Fatal("malformed config accepted")
		}
		content, _ := os.ReadFile(c.registry)
		if string(content) != registry {
			t.Fatal("foreign or malformed config changed")
		}
		content, _ = os.ReadFile(c.rules)
		if string(content) != "untouched" {
			t.Fatal("foreign rules changed")
		}
	}
}

func TestCleanupWSLJournalRetriesAndPreservesForeignBridge(t *testing.T) {
	root := t.TempDir()
	project := filepath.Join(root, "runtime")
	home := filepath.Join(root, "profile")
	// Linux profile names are transport names, independent of the native test OS.
	p := wslProfile{Distribution: "Ubuntu", Home: "/fixture", CodexHome: "/fixture/.codex"}
	local := func(value string) string {
		return filepath.Join(home, filepath.FromSlash(strings.TrimPrefix(value, p.Home)))
	}
	bp := backendPaths{ProjectDir: project, VenvPython: filepath.Join(project, "python"), DataDir: filepath.Join(project, "data")}
	old := runWSL
	t.Cleanup(func() { runWSL = old })
	runWSL = func(args ...string) (string, error) {
		value := args[len(args)-1]
		return local(value), nil
	}
	if err := applyWSLProfile(p, bp, "codex", local, func(string) error { return nil }); err != nil {
		t.Fatal(err)
	}
	record := wslRegistration{Profile: p, User: "custom-user", Target: "codex"}
	if err := saveWSLRegistration(project, record, false); err != nil {
		t.Fatal(err)
	}
	runWSL = func(...string) (string, error) { return "", errors.New("distribution unavailable") }
	if err := cleanupWSLRegistrations(project, []string{"codex"}); err == nil {
		t.Fatal("unavailable distro reported success")
	}
	records, _ := readWSLRegistrations(project)
	if len(records) != 1 {
		t.Fatal("retry journal lost")
	}
	runWSL = func(args ...string) (string, error) { return local(args[len(args)-1]), nil }
	if err := cleanupWSLRegistrations(project, []string{"codex"}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(local("/fixture/.codex/imprint-wsl")); !os.IsNotExist(err) {
		t.Fatal("bridge remains")
	}
	records, _ = readWSLRegistrations(project)
	if len(records) != 0 {
		t.Fatal("successful cleanup not recorded")
	}
	// Old installation must not disconnect the profile after relocation.
	bp.ProjectDir = filepath.Join(root, "new-runtime")
	if err := applyWSLProfile(p, bp, "codex", local, func(string) error { return nil }); err != nil {
		t.Fatal(err)
	}
	saveWSLRegistration(project, record, false)
	if err := cleanupWSLRegistrations(project, []string{"codex"}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(local("/fixture/.codex/imprint-wsl")); err != nil {
		t.Fatal("foreign bridge removed")
	}
}

func TestUninstallOnlyRemovesOwnedAliasesAndSymlinks(t *testing.T) {
	root := t.TempDir()
	project := filepath.Join(root, "runtime")
	rc := filepath.Join(root, "shellrc")
	original := "keep-before\n# imprint CLI alias\nalias imprint='" + filepath.ToSlash(project) + "/bin/imprint'\nkeep-after\n# imprint CLI alias\nalias imprint='/another/install/imprint'\n"
	writeCleanupFixture(t, rc, original)
	if err := removeManagedAlias(rc, project); err != nil {
		t.Fatal(err)
	}
	content, _ := os.ReadFile(rc)
	if strings.Contains(string(content), filepath.ToSlash(project)) || !strings.Contains(string(content), "keep-after") || !strings.Contains(string(content), "/another/install") {
		t.Fatal("bad alias cleanup")
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(filepath.Join(project, "bin", "imprint"), link); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	if err := removeOwnedSymlink(link, project); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(link); !os.IsNotExist(err) {
		t.Fatal("owned symlink remains")
	}
	if err := os.Symlink(filepath.Join(root, "foreign", "imprint"), link); err != nil {
		t.Fatal(err)
	}
	if err := removeOwnedSymlink(link, project); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(link); err != nil {
		t.Fatal("foreign symlink removed")
	}
}

// clineHome points Cline's environment at a disposable home.
func clineHome(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("APPDATA", filepath.Join(home, "AppData", "Roaming"))
	for _, name := range []string{"CLINE_DIR", "CLINE_DATA_DIR", "CLINE_SESSION_DATA_DIR", "CLINE_MCP_SETTINGS_PATH"} {
		t.Setenv(name, "")
	}
	return home
}

func TestCleanupPreservesSharedClineRuleForAnotherRuntime(t *testing.T) {
	clineHome(t)
	registrations := clineRegistrations()
	current, legacy := registrations[0], registrations[len(registrations)-1]
	writeCleanupFixture(t, current.rules, instructions.ClineRule)
	writeCleanupFixture(t, legacy.registry, `{"mcpServers":{"imprint":{"command":"/other/runtime/python"}}}`)
	if err := cleanupNativeClients(registrations, "/this/runtime"); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(current.rules)
	if err != nil || string(content) != instructions.ClineRule {
		t.Fatal("foreign client's shared Cline rule removed")
	}
}

// Disconnecting Cline removes Imprint from its MCP settings and rules, and
// what setup wrote before ADR 0056: the ~/.clinerules rule when it is the
// generated one, and the VS Code extension storage entries.
func TestCleanupRemovesClineRegistrationsOldAndNew(t *testing.T) {
	home := clineHome(t)
	project := filepath.Join(home, "runtime")
	t.Setenv("CLINE_MCP_SETTINGS_PATH", filepath.Join(home, "custom", "mcp.json"))
	owned := `{"mcpServers":{"imprint":{"command":"` + filepath.ToSlash(filepath.Join(project, "python")) + `"},"other":{"command":"keep-other"}}}`
	writeCleanupFixture(t, filepath.Join(home, "custom", "mcp.json"), owned)
	writeCleanupFixture(t, filepath.Join(home, ".cline", "rules", "imprint.md"), instructions.ClineRule)
	writeCleanupFixture(t, filepath.Join(home, ".clinerules", "imprint.md"), instructions.ClineRule)
	storage := ""
	for _, r := range clineRegistrations() {
		if strings.Contains(r.registry, "saoudrizwan.claude-dev") {
			storage = r.registry
			break
		}
	}
	writeCleanupFixture(t, storage, owned)
	selected, _ := selectClients("cline")
	if err := disconnectClients(selected, project); err != nil {
		t.Fatal(err)
	}
	for _, registry := range []string{filepath.Join(home, "custom", "mcp.json"), storage} {
		content, _ := os.ReadFile(registry)
		if strings.Contains(string(content), `"imprint"`) || !strings.Contains(string(content), "keep-other") {
			t.Fatalf("%s: %s", registry, content)
		}
	}
	for _, rule := range []string{filepath.Join(home, ".cline", "rules", "imprint.md"), filepath.Join(home, ".clinerules", "imprint.md")} {
		if _, err := os.Stat(rule); !os.IsNotExist(err) {
			t.Fatalf("generated rule %s remains", rule)
		}
	}

	// An edited rule in the old place is the user's now.
	writeCleanupFixture(t, filepath.Join(home, ".clinerules", "imprint.md"), instructions.ClineRule+"\nMy addition.\n")
	if err := disconnectClients(selected, project); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(home, ".clinerules", "imprint.md")); err != nil {
		t.Fatal("an edited rule was removed")
	}
}

func TestCleanupMigratesLegacySelectionBesideCustomProfile(t *testing.T) {
	root := t.TempDir()
	project := filepath.Join(root, "runtime")
	custom := wslRegistration{Profile: wslProfile{Distribution: "Ubuntu", Home: "/custom", CodexHome: "/custom/.codex"}, Target: "codex", CustomProfile: true}
	if err := saveWSLRegistration(project, custom, false); err != nil {
		t.Fatal(err)
	}
	writeCleanupFixture(t, filepath.Join(project, ".wsl-integrations.txt"), "Ubuntu|codex\n")
	old := runWSL
	t.Cleanup(func() { runWSL = old })
	probed := false
	runWSL = func(args ...string) (string, error) {
		if strings.Contains(strings.Join(args, " "), "imprint-probe") {
			probed = true
			return "/default\n\n/default/.codex\n/default/.claude.json\n\n\n\n", nil
		}
		return filepath.Join(root, "profiles", filepath.FromSlash(strings.TrimPrefix(args[len(args)-1], "/"))), nil
	}
	if err := cleanupWSLRegistrations(project, []string{"codex"}); err != nil {
		t.Fatal(err)
	}
	if !probed {
		t.Fatal("custom registration hid legacy default profile")
	}
	selections, _ := os.ReadFile(filepath.Join(project, ".wsl-integrations.txt"))
	if len(selections) != 0 {
		t.Fatal("repair can reconnect disabled client")
	}
}
