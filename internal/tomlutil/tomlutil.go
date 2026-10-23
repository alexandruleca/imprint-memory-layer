// Package tomlutil validates TOML and rewrites only an owned MCP server stanza.
package tomlutil

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/hunter/imprint/internal/configfile"
	"github.com/pelletier/go-toml/v2"
)

// EnsureMCPServer upserts an `[mcp_servers.<name>]` section with command,
// args, and env fields. Creates the file (and parent dir) if missing.
// Returns true if the file changed, false if the section was already identical.
//
// Section format emitted:
//
//	[mcp_servers.<name>]
//	command = "<command>"
//	args = ["...", "..."]
//	env = { KEY1 = "v1", KEY2 = "v2" }
//
// Keys in `env` are serialized in sorted order so re-runs are idempotent.
func EnsureMCPServer(path, name string, spec map[string]any) (bool, error) {

	var existing string
	if data, err := os.ReadFile(path); err == nil {
		existing = string(data)
	} else if !os.IsNotExist(err) {
		return false, err
	} else {
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			return false, fmt.Errorf("creating directory: %w", err)
		}
	}

	var document map[string]any
	if err := toml.Unmarshal([]byte(existing), &document); err != nil {
		return false, fmt.Errorf("invalid existing TOML: %w", err)
	}
	var old map[string]any
	if servers, ok := document["mcp_servers"].(map[string]any); ok {
		old, _ = servers[name].(map[string]any)
	}
	desired := make(map[string]any)
	for key, value := range spec {
		desired[key] = value
	}
	if previous, ok := old["env"].(map[string]any); ok {
		environment := make(map[string]any)
		for key, value := range previous {
			environment[key] = value
		}
		if supplied, ok := spec["env"].(map[string]any); ok {
			for key, value := range supplied {
				environment[key] = value
			}
		}
		desired["env"] = environment
	}
	block, err := renderSection(name, desired)
	if err != nil {
		return false, err
	}
	extras := make(map[string]any)
	for key, value := range old {
		if key != "command" && key != "args" && key != "env" {
			extras[key] = value
		}
	}
	if len(extras) > 0 {
		encoded, err := toml.Marshal(extras)
		if err != nil {
			return false, err
		}
		for _, line := range strings.Split(strings.TrimSpace(string(encoded)), "\n") {
			if strings.HasPrefix(line, "[") {
				prefix := 1
				if strings.HasPrefix(line, "[[") {
					prefix = 2
				}
				line = line[:prefix] + "mcp_servers." + name + "." + line[prefix:]
			}
			block += line + "\n"
		}
	}
	updated := rewriteMCP(existing, name, block)
	var validated map[string]any
	if err := toml.Unmarshal([]byte(updated), &validated); err != nil {
		return false, fmt.Errorf("refusing invalid TOML update: %w", err)
	}
	if updated == existing {
		return false, nil
	}
	return true, writeAtomic(path, updated)
}

// SetBoolInSection upserts a `key = true|false` line inside `[section]`.
// Creates the section if missing. Returns true if the file changed, false
// if the line was already identical. Preserves unrelated keys in the
// section and other sections.
func SetBoolInSection(path, section, key string, value bool) (bool, error) {
	var existing string
	if data, err := os.ReadFile(path); err == nil {
		existing = string(data)
	} else if !os.IsNotExist(err) {
		return false, err
	} else {
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			return false, fmt.Errorf("creating directory: %w", err)
		}
	}

	header := "[" + section + "]"
	valStr := "false"
	if value {
		valStr = "true"
	}
	desired := key + " = " + valStr

	start, end, found := findSection(existing, header)
	if !found {
		separator := "\n"
		if existing == "" || strings.HasSuffix(existing, "\n\n") {
			separator = ""
		} else if !strings.HasSuffix(existing, "\n") {
			separator = "\n\n"
		}
		updated := existing + separator + header + "\n" + desired + "\n"
		if updated == existing {
			return false, nil
		}
		return true, writeAtomic(path, updated)
	}

	// Section exists — scan its lines for an existing `key = ` entry.
	section_body := existing[start:end]
	lines := strings.Split(strings.TrimRight(section_body, "\n"), "\n")
	replaced := false
	for i, ln := range lines {
		trim := strings.TrimSpace(ln)
		if strings.HasPrefix(trim, key+" =") || strings.HasPrefix(trim, key+"=") {
			if trim == desired {
				return false, nil
			}
			lines[i] = desired
			replaced = true
			break
		}
	}
	if !replaced {
		lines = append(lines, desired)
	}
	newBody := strings.Join(lines, "\n") + "\n"
	updated := existing[:start] + newBody + existing[end:]
	if updated == existing {
		return false, nil
	}
	return true, writeAtomic(path, updated)
}

// RemoveMCPServer deletes the `[mcp_servers.<name>]` section from the file.
// Returns true if removed, false if absent or file missing.
func RemoveMCPServer(path, name string) (bool, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, err
	}
	existing := string(data)
	var validated map[string]any
	if err := toml.Unmarshal(data, &validated); err != nil {
		return false, err
	}
	updated := rewriteMCP(existing, name, "")
	if updated == existing {
		return false, nil
	}
	return true, writeAtomic(path, updated)
}

// sectionHeader returns the literal header line for a given MCP server name.
func sectionHeader(name string) string {
	return "[mcp_servers." + name + "]"
}

// findSection locates the byte range of an existing TOML section (header
// line through the newline before the next `[` header or EOF). Returns
// (start, end, found). start is the byte offset of the header line;
// end is the byte offset just past the section's trailing newline.
func findSection(content, header string) (int, int, bool) {
	scanner := bufio.NewScanner(strings.NewReader(content))
	// Raise the max line size — config values can be long (venv paths etc.).
	scanner.Buffer(make([]byte, 64*1024), 1024*1024)

	offset := 0
	startIdx := -1
	for scanner.Scan() {
		line := scanner.Text()
		lineLen := len(line) + 1 // +1 for the newline the scanner strips
		trim := strings.TrimSpace(line)
		if startIdx == -1 {
			if trim == header {
				startIdx = offset
			}
		} else if strings.HasPrefix(trim, "[") {
			return startIdx, offset, true
		}
		offset += lineLen
	}
	if startIdx >= 0 {
		return startIdx, len(content), true
	}
	return 0, 0, false
}

// upsertSection replaces or appends a rendered block addressed by header.
func upsertSection(content, header, block string) string {
	if start, end, found := findSection(content, header); found {
		trailing := ""
		if end < len(content) {
			trailing = content[end:]
		}
		out := content[:start] + block
		if trailing != "" {
			if !strings.HasSuffix(out, "\n") {
				out += "\n"
			}
			out += trailing
		} else if !strings.HasSuffix(out, "\n") {
			out += "\n"
		}
		return out
	}
	// Append.
	separator := "\n"
	if content == "" || strings.HasSuffix(content, "\n\n") {
		separator = ""
	} else if !strings.HasSuffix(content, "\n") {
		separator = "\n\n"
	}
	out := content + separator + block
	if !strings.HasSuffix(out, "\n") {
		out += "\n"
	}
	return out
}

// renderSection renders the TOML block for one server spec.
func renderSection(name string, spec map[string]any) (string, error) {
	var b strings.Builder
	b.WriteString(sectionHeader(name))
	b.WriteString("\n")

	cmd, _ := spec["command"].(string)
	if cmd == "" {
		return "", fmt.Errorf("spec missing string `command`")
	}
	fmt.Fprintf(&b, "command = %s\n", tomlString(cmd))

	argsAny, hasArgs := spec["args"]
	if hasArgs {
		args, err := toStringSlice(argsAny)
		if err != nil {
			return "", fmt.Errorf("args: %w", err)
		}
		b.WriteString("args = [")
		for i, a := range args {
			if i > 0 {
				b.WriteString(", ")
			}
			b.WriteString(tomlString(a))
		}
		b.WriteString("]\n")
	}

	envAny, hasEnv := spec["env"]
	if hasEnv {
		envMap, err := toStringMap(envAny)
		if err != nil {
			return "", fmt.Errorf("env: %w", err)
		}
		keys := make([]string, 0, len(envMap))
		for k := range envMap {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		b.WriteString("env = {")
		for i, k := range keys {
			if i > 0 {
				b.WriteString(", ")
			}
			fmt.Fprintf(&b, "%s = %s", k, tomlString(envMap[k]))
		}
		b.WriteString("}\n")
	}

	return b.String(), nil
}

// tomlString renders a TOML basic string literal, escaping backslashes and quotes.
func tomlString(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, `"`, `\"`)
	return `"` + s + `"`
}

func toStringSlice(v any) ([]string, error) {
	switch t := v.(type) {
	case []string:
		return t, nil
	case []any:
		out := make([]string, 0, len(t))
		for i, el := range t {
			s, ok := el.(string)
			if !ok {
				return nil, fmt.Errorf("element %d not a string", i)
			}
			out = append(out, s)
		}
		return out, nil
	default:
		return nil, fmt.Errorf("not a string slice (%T)", v)
	}
}

func toStringMap(v any) (map[string]string, error) {
	switch t := v.(type) {
	case map[string]string:
		return t, nil
	case map[string]any:
		out := make(map[string]string, len(t))
		for k, el := range t {
			s, ok := el.(string)
			if !ok {
				return nil, fmt.Errorf("value for %q not a string", k)
			}
			out[k] = s
		}
		return out, nil
	default:
		return nil, fmt.Errorf("not a string map (%T)", v)
	}
}

func writeAtomic(path, content string) error {
	return configfile.Write(path, []byte(content))
}

// rewriteMCP preserves bytes outside the owned server and its nested tables.
func rewriteMCP(content, name, replacement string) string {
	lines := strings.SplitAfter(content, "\n")
	var result strings.Builder
	inside := false
	replaced := false
	parent := sectionHeader(name)
	nested := "[mcp_servers." + name + "."
	for _, line := range lines {
		header := strings.TrimSpace(strings.SplitN(strings.TrimSpace(line), "#", 2)[0])
		if strings.HasPrefix(header, "[") {
			inside = header == parent || strings.HasPrefix(header, nested) || strings.HasPrefix(header, "[[mcp_servers."+name+".")
			if inside && !replaced {
				result.WriteString(replacement)
				replaced = true
			}
		}
		if !inside {
			result.WriteString(line)
		}
	}
	if !replaced && replacement != "" {
		if result.Len() > 0 && !strings.HasSuffix(result.String(), "\n\n") {
			result.WriteString("\n")
		}
		result.WriteString(replacement)
	}
	return result.String()
}
