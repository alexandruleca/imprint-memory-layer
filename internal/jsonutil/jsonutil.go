package jsonutil

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
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

// EnsureHook adds a hook to the hooks.<event> array in settings.json.
// It checks if a hook with the same command already exists and skips if so.
// Returns true if added, false if already present.
func EnsureHook(settingsPath, event, command string, timeout int) (bool, error) {
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

	newGroup := map[string]any{
		"hooks": []any{newHook},
	}

	hooks[event] = append(eventHooks, newGroup)
	return true, WriteJSON(settingsPath, data)
}
