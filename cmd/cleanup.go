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

func nativeCleanupTargets() []clientCleanup {
	home := platform.HomeDir()
	claudeDir := filepath.Join(home, ".claude")
	claudeRegistry := filepath.Join(home, ".claude.json")
	targets := []clientCleanup{
		{registry: claudeRegistry, keys: []string{"mcpServers"}, hooks: filepath.Join(claudeDir, "settings.json"), rules: filepath.Join(claudeDir, "CLAUDE.md"), permission: true},
		{registry: platform.CursorMCPPath(), keys: []string{"mcpServers"}, hooks: platform.CursorHooksPath(), rules: filepath.Join(platform.CursorRulesDir(), "imprint.mdc"), rule: instructions.CursorRule},
		{registry: platform.CodexConfigPath(), keys: []string{"mcp_servers"}, toml: true, hooks: platform.CodexHooksPath(), rules: platform.CodexAgentsPath()},
		{registry: platform.ClineCLISettingsPath(), keys: []string{"mcpServers"}, rules: platform.ClineRulesPath(), rule: instructions.ClineRule},
		{registry: platform.OpenClawMCPPath(), keys: []string{"mcp", "servers"}},
	}
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		targets = append(targets, clientCleanup{registry: filepath.Join(dir, ".claude.json"), keys: []string{"mcpServers"}, hooks: filepath.Join(dir, "settings.json"), rules: filepath.Join(dir, "CLAUDE.md"), permission: true})
	}
	if platform.CodexHome() != filepath.Join(home, ".codex") {
		dir := filepath.Join(home, ".codex")
		targets = append(targets, clientCleanup{registry: filepath.Join(dir, "config.toml"), keys: []string{"mcp_servers"}, toml: true, hooks: filepath.Join(dir, "hooks.json"), rules: filepath.Join(dir, "AGENTS.md")})
	}
	for _, file := range platform.ClaudeDesktopConfigPaths() {
		targets = append(targets, clientCleanup{registry: file, keys: []string{"mcpServers"}})
	}
	for _, dir := range platform.VSCodeUserDirs() {
		targets = append(targets,
			clientCleanup{registry: filepath.Join(dir, "mcp.json"), keys: []string{"servers"}, rules: filepath.Join(dir, "prompts", "imprint.instructions.md")},
			clientCleanup{registry: filepath.Join(dir, "globalStorage", "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json"), keys: []string{"mcpServers"}})
	}
	return targets
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
		content, err := os.ReadFile(c.registry)
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		if err == nil {
			var doc map[string]any
			if c.toml {
				err = toml.Unmarshal(content, &doc)
			} else {
				doc, err = jsonutil.ReadJSON(c.registry)
			}
			if err != nil {
				return fmt.Errorf("%s: %w", c.registry, err)
			}
			node := doc
			for _, key := range c.keys {
				node, _ = node[key].(map[string]any)
			}
			if entry, exists := node["imprint"]; exists {
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

func cleanupIntegrations(project string) error {
	var failures []error
	// Remove WSL first: unavailable distributions must keep a retryable runtime.
	if err := cleanupWSLRegistrations(project); err != nil {
		failures = append(failures, err)
	}
	if err := cleanupNativeClients(nativeCleanupTargets(), project); err != nil {
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
