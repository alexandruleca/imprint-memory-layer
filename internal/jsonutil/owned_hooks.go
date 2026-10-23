package jsonutil

import (
	"os"
	"strings"
)

func isImprintCommand(command string) bool {
	for _, marker := range []string{"imprint/hook_entry.py", `imprint\hook_entry.py`, "imprint-wsl' hook ", "imprint.cli_", "Imprint MCP available", "Imprint MCP gate:", "COMPACTION IMMINENT", "Imprint MCP: compaction"} {
		if strings.Contains(command, marker) {
			return true
		}
	}
	return false
}

// SetOwnedHookWithMatcher retains unrelated handlers even on the same matcher.
func SetOwnedHookWithMatcher(path, event, matcher, command string, timeout int, asynchronous bool) error {
	data, err := ReadJSON(path)
	if err != nil {
		if !os.IsNotExist(err) {
			return err
		}
		data = map[string]any{}
	}
	hooks, ok := data["hooks"].(map[string]any)
	if !ok {
		hooks = map[string]any{}
		data["hooks"] = hooks
	}
	groups, _ := hooks[event].([]any)
	hook := map[string]any{"type": "command", "command": command}
	if timeout > 0 {
		hook["timeout"] = timeout
	}
	if asynchronous {
		hook["async"] = true
	}
	inserted := false
	for _, value := range groups {
		group, ok := value.(map[string]any)
		if !ok || group["matcher"] != matcher && !(matcher == "" && group["matcher"] == nil) {
			continue
		}
		previous, _ := group["hooks"].([]any)
		keep := make([]any, 0, len(previous)+1)
		for _, value := range previous {
			current, ok := value.(map[string]any)
			cmd, _ := current["command"].(string)
			if !ok || !isImprintCommand(cmd) && cmd != command {
				keep = append(keep, value)
			}
		}
		if !inserted {
			keep = append(keep, hook)
			inserted = true
		}
		group["hooks"] = keep
	}
	if !inserted {
		group := map[string]any{"hooks": []any{hook}}
		if matcher != "" {
			group["matcher"] = matcher
		}
		groups = append(groups, group)
	}
	hooks[event] = groups
	return WriteJSON(path, data)
}

func SetOwnedCursorHook(path, event, matcher, command string) error {
	data, err := ReadJSON(path)
	if err != nil {
		if !os.IsNotExist(err) {
			return err
		}
		data = map[string]any{}
	}
	hooks, ok := data["hooks"].(map[string]any)
	if !ok {
		hooks = map[string]any{}
		data["hooks"] = hooks
	}
	entries, _ := hooks[event].([]any)
	keep := make([]any, 0, len(entries)+1)
	for _, value := range entries {
		entry, ok := value.(map[string]any)
		cmd, _ := entry["command"].(string)
		if !ok || !isImprintCommand(cmd) && cmd != command {
			keep = append(keep, value)
		}
	}
	entry := map[string]any{"command": command}
	if matcher != "" {
		entry["matcher"] = matcher
	}
	hooks[event] = append(keep, entry)
	if _, ok := data["version"]; !ok {
		data["version"] = 1
	}
	return WriteJSON(path, data)
}
