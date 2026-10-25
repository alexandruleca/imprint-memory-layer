package cmd

import (
	"encoding/json"
	"os"
	"path"
	"path/filepath"
	"strings"
	"testing"

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
		return "/home/test\n/home/test/.claude\n/home/test/.codex\n/home/test/.claude.json\n", nil
	}
	profiles := listWSL()
	if len(profiles) != 1 || profiles[0].Distribution != "Ubuntu" {
		t.Fatalf("wrong profiles: %v", profiles)
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
		return "/home/test\n/home/test/.claude\n\n/home/test/.claude.json\n/home/test/.cursor\n", nil
	}
	p, err := probeWSL("Ubuntu", "", "", "", "")
	if err != nil || p.CursorHome != "/home/test/.cursor" || p.CodexHome != "" {
		t.Fatalf("profile = %+v, %v", p, err)
	}
	if dir, err := wslClientDir(p, "cursor"); err != nil || dir != p.CursorHome {
		t.Fatalf("cursor store = %q, %v", dir, err)
	}
	if _, err := wslClientDir(p, "copilot"); err == nil {
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
	if err := cleanupWSLRegistrations(project); err != nil {
		t.Fatal(err)
	}
	if records, _ := readWSLRegistrations(project); len(records) != 0 {
		t.Fatalf("records = %+v", records)
	}
	if _, err := os.Stat(local(p.CursorHome)); err != nil {
		t.Fatal("the Cursor profile was removed")
	}
}
