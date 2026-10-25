package cmd

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/hunter/imprint/internal/configfile"
	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/tomlutil"
	"github.com/pelletier/go-toml/v2"
)

type clientCleanup struct {
	registry           string
	keys               []string
	toml               bool
	hooks, rules, rule string
	permission         bool
}

// The registrations of each client in the clients table: the files its
// setup handler writes, which disable cleans up and `imprint clients` reads.

func claudeCodeRegistrations() []clientCleanup {
	home := platform.HomeDir()
	claudeDir := filepath.Join(home, ".claude")
	registrations := []clientCleanup{{registry: filepath.Join(home, ".claude.json"), keys: []string{"mcpServers"}, hooks: filepath.Join(claudeDir, "settings.json"), rules: filepath.Join(claudeDir, "CLAUDE.md"), permission: true}}
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		registrations = append(registrations, clientCleanup{registry: filepath.Join(dir, ".claude.json"), keys: []string{"mcpServers"}, hooks: filepath.Join(dir, "settings.json"), rules: filepath.Join(dir, "CLAUDE.md"), permission: true})
	}
	return registrations
}

func claudeDesktopRegistrations() []clientCleanup {
	var registrations []clientCleanup
	for _, file := range platform.ClaudeDesktopConfigPaths() {
		registrations = append(registrations, clientCleanup{registry: file, keys: []string{"mcpServers"}})
	}
	return registrations
}

// chatGPTDesktopRegistrations: ChatGPT Desktop registers through the Codex
// profile it shares, so disconnecting it disconnects Codex too. From WSL its
// setup registers nothing (SetupChatGPTDesktop).
func chatGPTDesktopRegistrations() []clientCleanup {
	if platform.IsWSL() {
		return nil
	}
	return codexRegistrations()
}

func cursorRegistrations() []clientCleanup {
	return []clientCleanup{{registry: platform.CursorMCPPath(), keys: []string{"mcpServers"}, hooks: platform.CursorHooksPath(), rules: filepath.Join(platform.CursorRulesDir(), "imprint.mdc"), rule: instructions.CursorRule}}
}

func codexRegistrations() []clientCleanup {
	registrations := []clientCleanup{{registry: platform.CodexConfigPath(), keys: []string{"mcp_servers"}, toml: true, hooks: platform.CodexHooksPath(), rules: platform.CodexAgentsPath()}}
	home := platform.HomeDir()
	if platform.CodexHome() != filepath.Join(home, ".codex") {
		dir := filepath.Join(home, ".codex")
		registrations = append(registrations, clientCleanup{registry: filepath.Join(dir, "config.toml"), keys: []string{"mcp_servers"}, toml: true, hooks: filepath.Join(dir, "hooks.json"), rules: filepath.Join(dir, "AGENTS.md")})
	}
	return registrations
}

func copilotRegistrations() []clientCleanup {
	var registrations []clientCleanup
	for _, dir := range platform.VSCodeUserDirs() {
		registrations = append(registrations, clientCleanup{registry: filepath.Join(dir, "mcp.json"), keys: []string{"servers"}, rules: filepath.Join(dir, "prompts", "imprint.instructions.md")})
	}
	return registrations
}

func clineRegistrations() []clientCleanup {
	registrations := []clientCleanup{{registry: platform.ClineCLISettingsPath(), keys: []string{"mcpServers"}, rules: platform.ClineRulesPath(), rule: instructions.ClineRule}}
	for _, dir := range platform.VSCodeUserDirs() {
		registrations = append(registrations, clientCleanup{registry: filepath.Join(dir, "globalStorage", "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json"), keys: []string{"mcpServers"}})
	}
	return registrations
}

func openClawRegistrations() []clientCleanup {
	return []clientCleanup{{registry: platform.OpenClawMCPPath(), keys: []string{"mcp", "servers"}}}
}

// registrationsOf joins the selected clients' registrations; clients that
// share a registry (ChatGPT Desktop and Codex) list it once.
func registrationsOf(selected []client) []clientCleanup {
	seen := map[string]bool{}
	var registrations []clientCleanup
	for _, c := range selected {
		for _, r := range c.registrations() {
			if !seen[r.registry] {
				seen[r.registry] = true
				registrations = append(registrations, r)
			}
		}
	}
	return registrations
}

// registeredEntry returns the "imprint" server entry in c's registry, or nil
// when the registry or the entry does not exist.
func registeredEntry(c clientCleanup) (any, error) {
	content, err := os.ReadFile(c.registry)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var doc map[string]any
	if c.toml {
		err = toml.Unmarshal(content, &doc)
	} else {
		doc, err = jsonutil.ReadJSON(c.registry)
	}
	if err != nil {
		return nil, fmt.Errorf("%s: %w", c.registry, err)
	}
	node := doc
	for _, key := range c.keys {
		node, _ = node[key].(map[string]any)
	}
	return node["imprint"], nil
}

// isConnected reports whether this runtime is registered in any of the
// client's registries.
func isConnected(c client, project string) (bool, error) {
	var failures []error
	for _, r := range c.registrations() {
		entry, err := registeredEntry(r)
		if err != nil {
			failures = append(failures, err)
		} else if entry != nil && belongsToRuntime(entry, project) {
			return true, nil
		}
	}
	return false, errors.Join(failures...)
}

// belongsToRuntime handles native stdio entries and WSL desktop wrappers.
// A same-named entry belonging to another installation is left untouched.
func belongsToRuntime(value any, project string) bool {
	switch v := value.(type) {
	case string:
		v = strings.TrimPrefix(v, "PYTHONPATH=")
		v = strings.ReplaceAll(v, `\`, "/")
		project = strings.ReplaceAll(project, `\`, "/")
		if runtime.GOOS == "windows" {
			v, project = strings.ToLower(v), strings.ToLower(project)
		}
		project = strings.TrimRight(project, `/\\`)
		return v == project || strings.Contains(v, project+"/") || strings.Contains(v, project+`\`)
	case map[string]any:
		for _, item := range v {
			if belongsToRuntime(item, project) {
				return true
			}
		}
	case []any:
		for _, item := range v {
			if belongsToRuntime(item, project) {
				return true
			}
		}
	}
	return false
}

func cleanupClient(c clientCleanup, project string) error {
	if c.registry != "" {
		entry, err := registeredEntry(c)
		if err != nil {
			return err
		}
		if entry != nil {
			if !belongsToRuntime(entry, project) {
				output.Skip("Preserving another runtime's registration and rules in " + c.registry)
				return nil
			}
			if c.toml {
				_, err = tomlutil.RemoveMCPServer(c.registry, "imprint")
			} else {
				_, err = jsonutil.RemoveMCPServerNested(c.registry, c.keys, "imprint")
			}
			if err != nil {
				return err
			}
			output.Success("Removed Imprint from " + c.registry)
		}
	}
	if c.hooks != "" {
		// Current hook_entry paths and historical Imprint commands. This client
		// is known not to have a foreign Imprint registration at this point.
		_, err := jsonutil.RemoveHooksMatching(c.hooks, []string{"imprint/hook_entry.py", `imprint\hook_entry.py`, "imprint.cli_", "Imprint MCP available", "Imprint MCP gate", "COMPACTION IMMINENT", "Imprint MCP: compaction", filepath.Join(platform.DataDir(project), ".sessions"), filepath.ToSlash(filepath.Join(platform.DataDir(project), ".sessions"))})
		if err != nil {
			return fmt.Errorf("%s: %w", c.hooks, err)
		}
		if c.permission {
			if err := removePermission(c.hooks, "mcp__imprint__*"); err != nil {
				return err
			}
		}
	}
	if c.rules != "" {
		content, err := os.ReadFile(c.rules)
		if os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return err
		}
		if c.rule != "" {
			if string(content) == c.rule {
				return os.Remove(c.rules)
			}
			output.Skip("Preserving edited rule " + c.rules)
		} else if strings.Contains(string(content), instructions.MarkerStart) {
			if strings.Index(string(content), instructions.MarkerEnd) < strings.Index(string(content), instructions.MarkerStart) {
				return fmt.Errorf("incomplete Imprint markers in %s", c.rules)
			}
			return configfile.Write(c.rules, []byte(instructions.MergeManaged(string(content), "")))
		}
	}
	return nil
}

func removePermission(file, permission string) error {
	doc, err := jsonutil.ReadJSON(file)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	perms, _ := doc["permissions"].(map[string]any)
	allow, _ := perms["allow"].([]any)
	kept := make([]any, 0, len(allow))
	changed := false
	for _, entry := range allow {
		if text, ok := entry.(string); ok && text == permission {
			changed = true
		} else {
			kept = append(kept, entry)
		}
	}
	if !changed {
		return nil
	}
	perms["allow"] = kept
	return jsonutil.WriteJSON(file, doc)
}

// disconnectClients removes this runtime from the selected clients: their
// local registrations and the WSL profiles recorded for them.
func disconnectClients(selected []client, project string) error {
	var failures []error
	names := make([]string, 0, len(selected))
	for _, c := range selected {
		names = append(names, c.name)
	}
	// Remove WSL first: unavailable distributions must keep a retryable runtime.
	if err := cleanupWSLRegistrations(project, names); err != nil {
		failures = append(failures, err)
	}
	if err := cleanupNativeClients(registrationsOf(selected), project); err != nil {
		failures = append(failures, err)
	}
	return errors.Join(failures...)
}

func cleanupNativeClients(clients []clientCleanup, project string) error {
	// Cline extension and CLI share one global rule file. A foreign entry in
	// either client protects that shared rule even if the other is absent.
	protectClineRule := false
	for _, c := range clients {
		if filepath.Base(c.registry) != "cline_mcp_settings.json" {
			continue
		}
		doc, err := jsonutil.ReadJSON(c.registry)
		if err != nil && !os.IsNotExist(err) {
			protectClineRule = true
			continue
		}
		servers, _ := doc["mcpServers"].(map[string]any)
		if entry, exists := servers["imprint"]; exists && !belongsToRuntime(entry, project) {
			protectClineRule = true
		}
	}
	var failures []error
	for _, client := range clients {
		if protectClineRule && client.rule == instructions.ClineRule {
			client.rules = ""
		}
		if err := cleanupClient(client, project); err != nil {
			failures = append(failures, err)
		}
	}
	return errors.Join(failures...)
}
