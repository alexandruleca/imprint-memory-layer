package jsonutil

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// ReadJSON reads a JSON file into a map.
func ReadJSON(path string) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", path, err)
	}
	return result, nil
}

// WriteJSON writes a map to a JSON file atomically (write to tmp, then rename).
func WriteJSON(path string, data map[string]any) error {
	content, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return fmt.Errorf("marshaling JSON: %w", err)
	}
	content = append(content, '\n')

	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, content, 0644); err != nil {
		return fmt.Errorf("writing %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return fmt.Errorf("renaming %s to %s: %w", tmp, path, err)
	}
	return nil
}

// EnsurePermission adds a permission string to the permissions.allow array
// in a Claude Code settings.json file. Returns true if it was added, false if already present.
func EnsurePermission(settingsPath, perm string) (bool, error) {
	if _, err := os.Stat(settingsPath); os.IsNotExist(err) {
		// Create the directory if needed
		dir := filepath.Dir(settingsPath)
		if err := os.MkdirAll(dir, 0755); err != nil {
			return false, fmt.Errorf("creating directory %s: %w", dir, err)
		}
		// Create a minimal settings file
		data := map[string]any{
			"permissions": map[string]any{
				"allow": []any{perm},
			},
		}
		return true, WriteJSON(settingsPath, data)
	}

	data, err := ReadJSON(settingsPath)
	if err != nil {
		return false, err
	}

	// Navigate to permissions.allow, creating intermediate objects if needed
	permissions, ok := data["permissions"].(map[string]any)
	if !ok {
		permissions = map[string]any{}
		data["permissions"] = permissions
	}

	allowRaw, ok := permissions["allow"]
	if !ok {
		permissions["allow"] = []any{perm}
		return true, WriteJSON(settingsPath, data)
	}

	allow, ok := allowRaw.([]any)
	if !ok {
		permissions["allow"] = []any{perm}
		return true, WriteJSON(settingsPath, data)
	}

	// Check if already present
	for _, entry := range allow {
		if s, ok := entry.(string); ok && s == perm {
			return false, nil
		}
	}

	// Append
	permissions["allow"] = append(allow, perm)
	return true, WriteJSON(settingsPath, data)
}

// HookEntry represents a single Claude Code hook handler.
type HookEntry struct {
	Type    string `json:"type"`
	Command string `json:"command"`
	Timeout int    `json:"timeout,omitempty"`
}

// HookGroup represents a hook matcher + handlers array.
type HookGroup struct {
	Matcher string      `json:"matcher,omitempty"`
	Hooks   []HookEntry `json:"hooks"`
}

// SetHook replaces all hooks for an event with a single new hook.
// Unlike EnsureHook, this always overwrites — no duplicates possible.
func SetHook(settingsPath, event, command string, timeout int, async bool) error {
	data, err := ReadJSON(settingsPath)
	if err != nil {
		if os.IsNotExist(err) {
			data = map[string]any{}
		} else {
			return err
		}
	}

	hooks, ok := data["hooks"].(map[string]any)
	if !ok {
		hooks = map[string]any{}
		data["hooks"] = hooks
	}

	newHook := map[string]any{
		"type":    "command",
		"command": command,
	}
	if timeout > 0 {
		newHook["timeout"] = timeout
	}
	if async {
		newHook["async"] = true
	}

	hooks[event] = []any{
		map[string]any{
			"hooks": []any{newHook},
		},
	}

	return WriteJSON(settingsPath, data)
}

// SetHookWithMatcher replaces all hooks for an event/matcher pair with a single
// new hook. Existing groups for the same event with a different matcher are
// preserved; the group whose matcher equals `matcher` is replaced (or appended
// if absent). Use empty matcher for events without one (Stop, PreCompact,
// SessionStart-without-matcher).
func SetHookWithMatcher(settingsPath, event, matcher, command string, timeout int, async bool) error {
	data, err := ReadJSON(settingsPath)
	if err != nil {
		if os.IsNotExist(err) {
			data = map[string]any{}
		} else {
			return err
		}
	}

	hooks, ok := data["hooks"].(map[string]any)
	if !ok {
		hooks = map[string]any{}
		data["hooks"] = hooks
	}

	newHook := map[string]any{
		"type":    "command",
		"command": command,
	}
	if timeout > 0 {
		newHook["timeout"] = timeout
	}
	if async {
		newHook["async"] = true
	}

	newGroup := map[string]any{
		"hooks": []any{newHook},
	}
	if matcher != "" {
		newGroup["matcher"] = matcher
	}

	existing, _ := hooks[event].([]any)
	replaced := false
	for i, g := range existing {
		group, ok := g.(map[string]any)
		if !ok {
			continue
		}
		gm, _ := group["matcher"].(string)
		if gm == matcher {
			existing[i] = newGroup
			replaced = true
			break
		}
	}
	if !replaced {
		existing = append(existing, newGroup)
	}
	hooks[event] = existing

	return WriteJSON(settingsPath, data)
}

// RemoveHooksMatching strips every hook whose command string contains any of
// the given substrings. Empty groups are removed; events with no remaining
// groups are deleted. Returns the count of hook entries removed.
func RemoveHooksMatching(settingsPath string, needles []string) (int, error) {
	data, err := ReadJSON(settingsPath)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, err
	}
	hooks, ok := data["hooks"].(map[string]any)
	if !ok {
		return 0, nil
	}

	matches := func(cmd string) bool {
		for _, n := range needles {
			if n != "" && strings.Contains(cmd, n) {
				return true
			}
		}
		return false
	}

	removed := 0
	for event, raw := range hooks {
		groups, ok := raw.([]any)
		if !ok {
			continue
		}
		newGroups := make([]any, 0, len(groups))
		for _, g := range groups {
			grp, ok := g.(map[string]any)
			if !ok {
				continue
			}
			entries, _ := grp["hooks"].([]any)
			keep := make([]any, 0, len(entries))
			for _, h := range entries {
				hm, ok := h.(map[string]any)
				if !ok {
					keep = append(keep, h)
					continue
				}
				cmd, _ := hm["command"].(string)
				if matches(cmd) {
					removed++
					continue
				}
				keep = append(keep, h)
			}
			if len(keep) == 0 {
				continue
			}
			grp["hooks"] = keep
			newGroups = append(newGroups, grp)
		}
		if len(newGroups) == 0 {
			delete(hooks, event)
		} else {
			hooks[event] = newGroups
		}
	}

	if removed > 0 {
		if err := WriteJSON(settingsPath, data); err != nil {
			return removed, err
		}
	}
	return removed, nil
}

// EnsureMCPServer adds a server entry to mcpServers in a Cursor-style mcp.json.
// Returns true if the entry was added or updated, false if already identical.
// Creates the file (and parent dir) if missing.
func EnsureMCPServer(path, name string, spec map[string]any) (bool, error) {
	if _, err := os.Stat(path); os.IsNotExist(err) {
		dir := filepath.Dir(path)
		if err := os.MkdirAll(dir, 0755); err != nil {
			return false, fmt.Errorf("creating directory %s: %w", dir, err)
		}
		data := map[string]any{
			"mcpServers": map[string]any{name: spec},
		}
		return true, WriteJSON(path, data)
	}

	data, err := ReadJSON(path)
	if err != nil {
		return false, err
	}

	servers, ok := data["mcpServers"].(map[string]any)
	if !ok {
		servers = map[string]any{}
		data["mcpServers"] = servers
	}

	if existing, ok := servers[name].(map[string]any); ok {
		if jsonEqual(existing, spec) {
			return false, nil
		}
	}
	servers[name] = spec
	return true, WriteJSON(path, data)
}

func jsonEqual(a, b map[string]any) bool {
	ja, err1 := json.Marshal(a)
	jb, err2 := json.Marshal(b)
	if err1 != nil || err2 != nil {
		return false
	}
	return string(ja) == string(jb)
}

// EnsureHook adds a hook to the hooks.<event> array in settings.json.
// It checks if a hook with the same command already exists and skips if so.
// Returns true if added, false if already present.
func EnsureHook(settingsPath, event, command string, timeout int, async bool) (bool, error) {
	data, err := ReadJSON(settingsPath)
	if err != nil {
		if os.IsNotExist(err) {
			data = map[string]any{}
		} else {
			return false, err
		}
	}

	// Navigate to hooks.<event>, creating intermediate structures if needed
	hooks, ok := data["hooks"].(map[string]any)
	if !ok {
		hooks = map[string]any{}
		data["hooks"] = hooks
	}

	eventHooksRaw, ok := hooks[event]
	var eventHooks []any
	if ok {
		eventHooks, _ = eventHooksRaw.([]any)
	}

	// Check if a hook with this command already exists
	for _, groupRaw := range eventHooks {
		group, ok := groupRaw.(map[string]any)
		if !ok {
			continue
		}
		innerHooks, ok := group["hooks"].([]any)
		if !ok {
			continue
		}
		for _, h := range innerHooks {
			hook, ok := h.(map[string]any)
			if !ok {
				continue
			}
			if cmd, ok := hook["command"].(string); ok && cmd == command {
				return false, nil
			}
		}
	}

	// Build the new hook group
	newHook := map[string]any{
		"type":    "command",
		"command": command,
	}
	if timeout > 0 {
		newHook["timeout"] = timeout
	}
	if async {
		newHook["async"] = true
	}

	newGroup := map[string]any{
		"hooks": []any{newHook},
	}

	hooks[event] = append(eventHooks, newGroup)
	return true, WriteJSON(settingsPath, data)
}
