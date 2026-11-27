package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hunter/imprint/internal/instructions"
)

func isShort(rule string) bool {
	return strings.Contains(rule, "Load the `imprint` skill") && !strings.Contains(rule, "## Do NOT store")
}

// Clients that load skills get the short block; a client the user lists in
// instructions.full_rules, and every client that loads none, the full one
// (ADR 0214).
func TestEachClientGetsTheRulesBlockItLoads(t *testing.T) {
	t.Setenv("IMPRINT_INSTRUCTIONS_FULL_RULES", "")
	os.Unsetenv("IMPRINT_INSTRUCTIONS_FULL_RULES") // Set, even empty, it wins over config.json, as in Python.
	data := t.TempDir()
	for _, client := range []string{"claude-code", "codex", "cursor", "copilot", "opencode"} {
		if rule := rulesFor(client, data); !isShort(rule) {
			t.Fatalf("%s got the full block: %s", client, rule)
		}
	}
	if rule := rulesFor("cline", data); isShort(rule) || rule != instructions.ClineRule {
		t.Fatalf("cline: %s", rule)
	}
	if !strings.HasPrefix(rulesFor("cursor", data), "---\ndescription:") || !strings.Contains(rulesFor("opencode", data), "imprint") {
		t.Fatal("client framing lost")
	}
	if err := os.WriteFile(filepath.Join(data, "config.json"), []byte(`{"instructions":{"full_rules":"codex, cursor"}}`), 0600); err != nil {
		t.Fatal(err)
	}
	if rulesFor("codex", data) != instructions.CodexRule || rulesFor("cursor", data) != instructions.CursorRule || !isShort(rulesFor("copilot", data)) {
		t.Fatal("instructions.full_rules not honoured")
	}
	t.Setenv("IMPRINT_INSTRUCTIONS_FULL_RULES", "copilot")
	if !isShort(rulesFor("codex", data)) || rulesFor("copilot", data) != instructions.CopilotRule {
		t.Fatal("the environment does not take precedence over config.json")
	}
	// A data folder that is not a path on this system, as a WSL profile's
	// Windows folder in a Linux test, reads and writes nothing.
	if !isShort(rulesFor("codex", `C:\not here`)) {
		t.Fatal("relative data folder")
	}
}

func TestTheFirstSwitchToTheShortBlockIsRecordedOncePerClient(t *testing.T) {
	t.Setenv("IMPRINT_INSTRUCTIONS_FULL_RULES", "codex")
	data := t.TempDir()
	read := func() map[string]map[string]any {
		raw, err := os.ReadFile(filepath.Join(data, "instructions.json"))
		if err != nil {
			t.Fatal(err)
		}
		var doc struct {
			Clients map[string]map[string]any `json:"clients"`
		}
		if err := json.Unmarshal(raw, &doc); err != nil {
			t.Fatal(err)
		}
		return doc.Clients
	}
	rulesFor("claude-code", data)
	rulesFor("codex", data)
	rulesFor("cline", data)
	first := read()
	if since, _ := first["claude-code"]["short_since"].(float64); since <= 0 || len(first) != 1 {
		t.Fatalf("recorded: %v", first)
	}
	if err := os.WriteFile(filepath.Join(data, "instructions.json"),
		[]byte(`{"clients":{"claude-code":{"short_since":1000,"decision":{"outcome":"kept"}}},"other":true}`), 0600); err != nil {
		t.Fatal(err)
	}
	rulesFor("claude-code", data)
	rulesFor("cursor", data)
	again := read()
	if kept, _ := again["claude-code"]["short_since"].(float64); kept != 1000 {
		t.Fatalf("re-recorded: %v", again)
	}
	if since, _ := again["cursor"]["short_since"].(float64); since <= 1000 {
		t.Fatalf("re-recorded: %v", again)
	}
	raw, _ := os.ReadFile(filepath.Join(data, "instructions.json"))
	if !strings.Contains(string(raw), `"outcome":"kept"`) && !strings.Contains(string(raw), `"outcome": "kept"`) || !strings.Contains(string(raw), `"other"`) {
		t.Fatalf("lost what upkeep recorded: %s", raw)
	}
}

// Disconnect removes the rule files Imprint owns whole in either form.
func TestCleanupRemovesOwnedRulesInTheShortForm(t *testing.T) {
	home := clineHome(t)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, ".config"))
	t.Setenv("OPENCODE_CONFIG_DIR", "")
	for client, registrations := range map[string][]clientCleanup{"cursor": cursorRegistrations(), "opencode": openCodeRegistrations()} {
		c := registrations[0]
		c.registry, c.hooks, c.owned, c.instruction = "", "", "", ""
		writeCleanupFixture(t, c.rules, instructions.Rule(client, false))
		if err := cleanupClient(c, t.TempDir()); err != nil {
			t.Fatal(err)
		}
		if _, err := os.Stat(c.rules); !os.IsNotExist(err) {
			t.Fatalf("%s: the short rule file remains", client)
		}
	}
}
