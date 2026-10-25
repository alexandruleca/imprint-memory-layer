package cmd

import (
	"encoding/json"
	"errors"
	"os"
	"path"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
)

func TestWSLDiscoveryUsesExplicitDistroAndSkipsDocker(t *testing.T) {
	old := runWSL
	t.Cleanup(func() { runWSL = old })
	runWSL = func(args ...string) (string, error) {
		if strings.Join(args, " ") == "--list --quiet" {
			return "Ubuntu\r\ndocker-desktop\r\n", nil
		}
		if args[0] != "--distribution" || args[1] != "Ubuntu" {
			t.Fatalf("unsafe discovery: %v", args)
		}
		return "/home/test\n/home/test/.claude\n/home/test/.codex\n/home/test/.claude.json\n\n\n\n", nil
	}
	listing := listWSL(t.TempDir())
	if len(listing.Distributions) != 1 || listing.Distributions[0].Distribution != "Ubuntu" || listing.Distributions[0].Error != "" {
		t.Fatalf("wrong profiles: %+v", listing)
	}
	if decodeWSL([]byte{'U', 0, 'b', 0, 'u', 0, 'n', 0, 't', 0, 'u', 0}) != "Ubuntu" {
		t.Fatal("UTF-16 list not decoded")
	}
	for _, name := range []string{"../Ubuntu", "bad|name", "bad\nname", `bad\name`, ""} {
		if validDistro(name) {
			t.Fatalf("accepted %q", name)
		}
	}
}

func TestWSLProfilesPreserveSettingsAndRemainIdempotent(t *testing.T) {
	for _, target := range []string{"claude-code", "codex"} {
		t.Run(target, func(t *testing.T) {
			root := t.TempDir()
			home := "/home/test's profile"
			p := wslProfile{Distribution: "Ubuntu", Home: home, ClaudeHome: home + "/.claude", CodexHome: home + "/.codex"}
			local := func(value string) string {
				return filepath.Join(root, filepath.FromSlash(strings.TrimPrefix(value, "/")))
			}
			for _, dir := range []string{p.ClaudeHome, p.CodexHome} {
				if err := os.MkdirAll(local(dir), 0700); err != nil {
					t.Fatal(err)
				}
			}
			hooksFile := path.Join(p.ClaudeHome, "settings.json")
			rules := path.Join(p.ClaudeHome, "CLAUDE.md")
			if target == "codex" {
				hooksFile = path.Join(p.CodexHome, "hooks.json")
				rules = path.Join(p.CodexHome, "AGENTS.md")
				if err := os.WriteFile(local(path.Join(p.CodexHome, "config.toml")), []byte("# keep\nmodel = \"custom\"\n[mcp_servers.other]\ncommand = \"other\"\n"), 0600); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := os.WriteFile(local(path.Join(home, ".claude.json")), []byte(`{"theme":"custom","mcpServers":{"other":{"command":"other"}}}`), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if err := os.WriteFile(local(hooksFile), []byte(`{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"unrelated-hook"}]}]},"custom":true}`), 0600); err != nil {
				t.Fatal(err)
			}
			os.WriteFile(local(rules), []byte("User-authored guidance.\n"), 0600)
			bp := backendPaths{ProjectDir: `C:\User's Imprint`, VenvPython: `C:\User's Imprint\.venv\Scripts\python.exe`, DataDir: `C:\memory store`}
			apply := func() {
				t.Helper()
				if err := applyWSLProfile(p, bp, target, local, func(string) error { return nil }); err != nil {
					t.Fatal(err)
				}
			}
			apply()
			first, _ := os.ReadFile(local(hooksFile))
			apply()
			second, _ := os.ReadFile(local(hooksFile))
			if string(first) != string(second) {
				t.Fatal("registration is not idempotent")
			}
			if !strings.Contains(string(second), "unrelated-hook") || !strings.Contains(string(second), "imprint-wsl") {
				t.Fatal("lost unrelated hook")
			}
			content, _ := os.ReadFile(local(rules))
			if !strings.Contains(string(content), "User-authored guidance.") {
				t.Fatal("lost user instructions")
			}
			if target == "codex" {
				content, _ := os.ReadFile(local(path.Join(p.CodexHome, "config.toml")))
				if !strings.Contains(string(content), "[mcp_servers.other]") {
					t.Fatal("lost other MCP server")
				}
			} else {
				data, err := jsonutil.ReadJSON(local(path.Join(home, ".claude.json")))
				if err != nil {
					t.Fatal(err)
				}
				if data["theme"] != "custom" {
					t.Fatal("lost custom settings")
				}
			}
			// Relocation replaces the owned hook instead of accumulating old bridges.
			bp.ProjectDir = `D:\Relocated Imprint`
			apply()
			data, err := jsonutil.ReadJSON(local(hooksFile))
			if err != nil {
				t.Fatal(err)
			}
			encoded, _ := json.Marshal(data)
			if strings.Count(string(encoded), "imprint-wsl' hook stop") != 1 {
				t.Fatalf("duplicate stop hook: %s", encoded)
			}
			if err := removeWSLProfile(p, target, local); err != nil {
				t.Fatal(err)
			}
			remaining, _ := os.ReadFile(local(hooksFile))
			if !strings.Contains(string(remaining), "unrelated-hook") || strings.Contains(string(remaining), "imprint-wsl") {
				t.Fatal("removal must retain unrelated hooks")
			}
			remaining, _ = os.ReadFile(local(rules))
			if !strings.Contains(string(remaining), "User-authored guidance.") || strings.Contains(string(remaining), "IMPRINT") {
				t.Fatal("removal must retain user-authored instructions")
			}
		})
	}
}

func TestWSLLauncherShellSyntaxAndDataIsolation(t *testing.T) {
	p := wslProfile{Distribution: "Ubuntu", Home: "/home/test"}
	bp := backendPaths{ProjectDir: `C:\with ' apostrophe`, VenvPython: `C:\with ' apostrophe\python.exe`, DataDir: `C:\with spaces\data`}
	script := wslLauncher(p, bp)
	for _, part := range []string{"wslpath -u", "exec \"$python\"", "--data-dir", "--cwd \"$PWD\"", "\"$@\""} {
		if !strings.Contains(script, part) {
			t.Fatalf("missing %s", part)
		}
	}
	if got := wslUNC("Ubuntu", "/home/user/../project"); got != `\\wsl.localhost\Ubuntu\home\project` {
		t.Fatal(got)
	}
}

func TestWSLRegistrationDoesNotOverwriteForeignLauncherOrMalformedConfig(t *testing.T) {
	root := t.TempDir()
	p := wslProfile{Distribution: "Ubuntu", Home: "/fixture", CodexHome: "/fixture/.codex"}
	local := func(value string) string {
		return filepath.Join(root, filepath.FromSlash(strings.TrimPrefix(value, "/")))
	}
	os.MkdirAll(local(p.CodexHome), 0700)
	launcher := local(p.CodexHome + "/imprint-wsl")
	os.WriteFile(launcher, []byte("user launcher\n"), 0600)
	bp := backendPaths{ProjectDir: "/runtime", VenvPython: "/python", DataDir: "/data"}
	if err := applyWSLProfile(p, bp, "codex", local, func(string) error { return nil }); err == nil {
		t.Fatal("foreign launcher overwritten")
	}
	after, _ := os.ReadFile(launcher)
	if string(after) != "user launcher\n" {
		t.Fatal("foreign launcher changed")
	}
	os.Remove(launcher)
	config := local(p.CodexHome + "/config.toml")
	original := []byte("[broken\n")
	os.WriteFile(config, original, 0600)
	if err := applyWSLProfile(p, bp, "codex", local, func(string) error { return nil }); err == nil {
		t.Fatal("malformed TOML accepted")
	}
	after, _ = os.ReadFile(config)
	if string(after) != string(original) {
		t.Fatal("malformed config overwritten")
	}
}

func TestWSLCursorRegistrationRecordsItsStoreAndWritesNothing(t *testing.T) {
	old := runWSL
	t.Cleanup(func() { runWSL = old })
	runWSL = func(args ...string) (string, error) {
		return "/home/test\n/home/test/.claude\n\n/home/test/.claude.json\n/home/test/.cursor\n\n\n", nil
	}
	p, err := probeWSL("Ubuntu", "", "", "", "")
	if err != nil || p.CursorHome != "/home/test/.cursor" || p.CodexHome != "" {
		t.Fatalf("profile = %+v, %v", p, err)
	}
	if dir, err := wslClientDir(p, "cursor"); err != nil || dir != p.CursorHome {
		t.Fatalf("cursor store = %q, %v", dir, err)
	}
	if _, err := wslClientDir(p, "vim"); err == nil {
		t.Fatal("an unknown target was accepted")
	}

	root := t.TempDir()
	project := filepath.Join(root, "runtime")
	local := func(value string) string { return filepath.Join(root, "profile", filepath.FromSlash(value)) }
	if err := os.MkdirAll(local(p.CursorHome), 0700); err != nil {
		t.Fatal(err)
	}
	bp := backendPaths{ProjectDir: project, VenvPython: filepath.Join(project, "python"), DataDir: filepath.Join(project, "data")}
	if err := applyWSLProfile(p, bp, "cursor", local, func(string) error { t.Fatal("nothing to make executable"); return nil }); err != nil {
		t.Fatal(err)
	}
	if entries, _ := os.ReadDir(local(p.CursorHome)); len(entries) != 0 {
		t.Fatalf("registering Cursor wrote %v", entries)
	}
	if err := applyWSLProfile(wslProfile{Distribution: "Ubuntu", Home: "/home/test"}, bp, "cursor", local, nil); err == nil {
		t.Fatal("a profile without Cursor was registered")
	}

	// Disable drops the record and touches nothing else.
	if err := saveWSLRegistration(project, wslRegistration{Profile: p, Target: "cursor"}, false); err != nil {
		t.Fatal(err)
	}
	runWSL = func(args ...string) (string, error) { return local(args[len(args)-1]), nil }
	if err := cleanupWSLRegistrations(project, wslTargets()); err != nil {
		t.Fatal(err)
	}
	if records, _ := readWSLRegistrations(project); len(records) != 0 {
		t.Fatalf("records = %+v", records)
	}
	if _, err := os.Stat(local(p.CursorHome)); err != nil {
		t.Fatal("the Cursor profile was removed")
	}
}

// Cline runs inside WSL: its probed directory gets the bridge launcher, the
// MCP entry in its data directory and the rule in its rules directory, and
// no hooks (ADR 0056).
func TestWSLClineRegistrationWritesItsSettingsAndRule(t *testing.T) {
	old := runWSL
	t.Cleanup(func() { runWSL = old })
	var probe []string
	runWSL = func(args ...string) (string, error) {
		probe = args
		return "/home/test\n\n\n/home/test/.claude.json\n\n/home/test/cline dir\n\n", nil
	}
	p, err := probeWSL("Ubuntu", "", "", "", "")
	if err != nil || p.ClineHome != "/home/test/cline dir" || p.ClaudeHome != "" {
		t.Fatalf("profile = %+v, %v", p, err)
	}
	if script := strings.Join(probe, " "); !strings.Contains(script, `cline=${CLINE_DIR:-$home/.cline}`) {
		t.Fatalf("probe does not honor CLINE_DIR: %s", script)
	}

	root := t.TempDir()
	local := func(value string) string {
		return filepath.Join(root, filepath.FromSlash(strings.TrimPrefix(value, "/")))
	}
	settings := local(path.Join(p.ClineHome, "data", "settings", "cline_mcp_settings.json"))
	rule := local(path.Join(p.ClineHome, "rules", "imprint.md"))
	launcher := path.Join(p.ClineHome, "imprint-wsl")
	writeCleanupFixture(t, settings, `{"mcpServers":{"other":{"command":"keep-other"},`+
		`"imprint":{"transport":{"type":"stdio","command":"/stale/imprint"},"disabled":true,"timeout":90}},"keep":true}`)
	writeCleanupFixture(t, local(path.Join(p.ClineHome, "rules", "mine.md")), "User rule.\n")
	bp := backendPaths{ProjectDir: `C:\Imprint`, VenvPython: `C:\Imprint\.venv\Scripts\python.exe`, DataDir: `C:\Imprint\data`}
	var executable []string
	apply := func() {
		t.Helper()
		if err := applyWSLProfile(p, bp, "cline", local, func(value string) error { executable = append(executable, value); return nil }); err != nil {
			t.Fatal(err)
		}
	}
	apply()
	first, _ := os.ReadFile(settings)
	apply()
	second, _ := os.ReadFile(settings)
	if string(first) != string(second) {
		t.Fatal("registration is not idempotent")
	}
	data, err := jsonutil.ReadJSON(settings)
	if err != nil {
		t.Fatal(err)
	}
	servers := data["mcpServers"].(map[string]any)
	entry := servers["imprint"].(map[string]any)
	if entry["command"] != launcher || entry["transport"] != nil || entry["disabled"] != true || entry["timeout"] != float64(90) {
		t.Fatalf("imprint entry = %v", entry)
	}
	if servers["other"] == nil || data["keep"] != true {
		t.Fatalf("lost unrelated settings: %v", data)
	}
	if content, _ := os.ReadFile(rule); string(content) != instructions.ClineRule {
		t.Fatalf("rule = %q", content)
	}
	if len(executable) != 2 || executable[0] != launcher {
		t.Fatalf("launcher made executable as %v", executable)
	}
	if entries, _ := os.ReadDir(local(p.ClineHome)); len(entries) != 3 { // data, rules, imprint-wsl
		t.Fatalf("Cline profile holds %v", entries)
	}

	if err := removeWSLProfile(p, "cline", local); err != nil {
		t.Fatal(err)
	}
	data, _ = jsonutil.ReadJSON(settings)
	if servers := data["mcpServers"].(map[string]any); servers["imprint"] != nil || servers["other"] == nil {
		t.Fatalf("removal left %v", servers)
	}
	for _, gone := range []string{rule, local(launcher)} {
		if _, err := os.Stat(gone); !os.IsNotExist(err) {
			t.Fatalf("%s remains", gone)
		}
	}
	if content, _ := os.ReadFile(local(path.Join(p.ClineHome, "rules", "mine.md"))); string(content) != "User rule.\n" {
		t.Fatal("removal touched the user's rule")
	}

	// An entry that now points elsewhere belongs to another runtime.
	apply()
	writeCleanupFixture(t, settings, `{"mcpServers":{"imprint":{"command":"/other/runtime"}}}`)
	if err := removeWSLProfile(p, "cline", local); err == nil {
		t.Fatal("removed another runtime's entry")
	}
}

// Copilot runs inside WSL: its probed home gets the bridge launcher, the MCP
// entry, the managed instructions block and Imprint's hooks file, whose
// commands call the launcher (ADR 0057).
func TestWSLCopilotRegistrationWritesItsHome(t *testing.T) {
	old := runWSL
	t.Cleanup(func() { runWSL = old })
	var probe []string
	runWSL = func(args ...string) (string, error) {
		probe = args
		return "/home/test\n\n\n/home/test/.claude.json\n\n\n/home/test/copilot's home\n", nil
	}
	p, err := probeWSL("Ubuntu", "", "", "", "")
	if err != nil || p.CopilotHome != "/home/test/copilot's home" {
		t.Fatalf("profile = %+v, %v", p, err)
	}
	if script := strings.Join(probe, " "); !strings.Contains(script, `copilot=${COPILOT_HOME:-$home/.copilot}`) {
		t.Fatalf("probe does not honor COPILOT_HOME: %s", script)
	}

	root := t.TempDir()
	local := func(value string) string {
		return filepath.Join(root, filepath.FromSlash(strings.TrimPrefix(value, "/")))
	}
	registry := local(path.Join(p.CopilotHome, "mcp-config.json"))
	rules := local(path.Join(p.CopilotHome, "copilot-instructions.md"))
	hooksFile := local(path.Join(p.CopilotHome, "hooks", "imprint.json"))
	launcher := path.Join(p.CopilotHome, "imprint-wsl")
	writeCleanupFixture(t, registry, `{"mcpServers":{"other":{"type":"stdio","command":"keep-other","tools":["*"]}}}`)
	writeCleanupFixture(t, rules, "User guidance.\n")
	bp := backendPaths{ProjectDir: `C:\Imprint`, VenvPython: `C:\Imprint\.venv\Scripts\python.exe`, DataDir: `C:\Imprint\data`}
	apply := func() {
		t.Helper()
		if err := applyWSLProfile(p, bp, "copilot", local, func(string) error { return nil }); err != nil {
			t.Fatal(err)
		}
	}
	apply()
	first, _ := os.ReadFile(hooksFile)
	apply()
	second, _ := os.ReadFile(hooksFile)
	if string(first) != string(second) {
		t.Fatal("registration is not idempotent")
	}
	data, err := jsonutil.ReadJSON(registry)
	if err != nil {
		t.Fatal(err)
	}
	entry := data["mcpServers"].(map[string]any)["imprint"].(map[string]any)
	if entry["command"] != launcher || entry["type"] != "stdio" || len(entry["tools"].([]any)) != 1 || data["mcpServers"].(map[string]any)["other"] == nil {
		t.Fatalf("mcp-config.json = %v", data)
	}
	if content, _ := os.ReadFile(rules); !strings.HasPrefix(string(content), "User guidance.\n") || !strings.Contains(string(content), instructions.MarkerStart) {
		t.Fatalf("instructions = %q", content)
	}
	if string(second) != string(copilotHooks(shellQuote(launcher)+" hook copilot-session-start", shellQuote(launcher)+" hook stop")) {
		t.Fatalf("hooks = %s", second)
	}

	if err := removeWSLProfile(p, "copilot", local); err != nil {
		t.Fatal(err)
	}
	data, _ = jsonutil.ReadJSON(registry)
	if servers := data["mcpServers"].(map[string]any); servers["imprint"] != nil || servers["other"] == nil {
		t.Fatalf("removal left %v", servers)
	}
	if content, _ := os.ReadFile(rules); !strings.HasPrefix(string(content), "User guidance.\n") || strings.Contains(string(content), instructions.MarkerStart) {
		t.Fatalf("instructions after removal = %q", content)
	}
	for _, gone := range []string{hooksFile, local(launcher)} {
		if _, err := os.Stat(gone); !os.IsNotExist(err) {
			t.Fatalf("%s remains", gone)
		}
	}
}

// The listing names every WSL target from the clients table, each
// distribution's profiles, and which of them this installation enrolled.
func TestWSLListingReportsProfilesAndEnrollment(t *testing.T) {
	old := runWSL
	t.Cleanup(func() { runWSL = old })
	runWSL = func(args ...string) (string, error) {
		if strings.Join(args, " ") == "--list --quiet" {
			return "Ubuntu\r\nDebian\r\nrancher-desktop\r\n", nil
		}
		if args[1] == "Debian" {
			return "", errors.New("distribution unavailable")
		}
		return "/home/me\n/home/me/.claude\n\n/home/me/.claude.json\n/home/me/.cursor\n/home/me/.cline\n\n", nil
	}
	project := t.TempDir()
	ubuntu := wslProfile{Distribution: "Ubuntu", Home: "/home/me", ClaudeHome: "/home/me/.claude", CursorHome: "/home/me/.cursor"}
	for _, r := range []wslRegistration{
		{Profile: ubuntu, Target: "claude-code"},
		{Profile: ubuntu, Target: "codex", User: "someone-else"},
	} {
		if err := saveWSLRegistration(project, r, false); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(project, ".wsl-integrations.txt"), []byte("Ubuntu|cursor\n"), 0600); err != nil {
		t.Fatal(err)
	}

	listing := listWSL(project)
	encoded, _ := json.Marshal(listing)
	want := `{"targets":[{"name":"claude-code","label":"Claude Code"},{"name":"cursor","label":"Cursor"},{"name":"codex","label":"Codex"},{"name":"copilot","label":"GitHub Copilot"},{"name":"cline","label":"Cline"}],` +
		`"distributions":[{"distribution":"Ubuntu","home":"/home/me","clients":[` +
		`{"name":"claude-code","profile":"/home/me/.claude","enrolled":true},` +
		`{"name":"cursor","profile":"/home/me/.cursor","enrolled":true},` +
		`{"name":"codex","profile":"","enrolled":false},` +
		`{"name":"copilot","profile":"","enrolled":false},` +
		`{"name":"cline","profile":"/home/me/.cline","enrolled":false}]},` +
		`{"distribution":"Debian","error":"distribution unavailable","clients":[` +
		`{"name":"claude-code","profile":"","enrolled":false},{"name":"cursor","profile":"","enrolled":false},{"name":"codex","profile":"","enrolled":false},` +
		`{"name":"copilot","profile":"","enrolled":false},{"name":"cline","profile":"","enrolled":false}]}]}`
	if string(encoded) != want {
		t.Fatalf("listing\n got %s\nwant %s", encoded, want)
	}

	runWSL = func(...string) (string, error) { return "", errors.New("WSL is not installed") }
	if listing := listWSL(project); listing.Error != "WSL discovery unavailable: WSL is not installed" || len(listing.Distributions) != 0 {
		t.Fatalf("unavailable WSL: %+v", listing)
	}
}
