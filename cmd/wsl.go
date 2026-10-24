package cmd

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"runtime"
	"strings"
	"time"
	"unicode/utf16"

	"github.com/hunter/imprint/internal/configfile"
	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/tomlutil"
	"github.com/pelletier/go-toml/v2"
)

// WSL registrations are explicit and never part of setup all's implicit scan.
// WSL itself chooses the distro's default user unless --user is supplied.
type wslProfile struct {
	Distribution   string `json:"distribution"`
	Home           string `json:"home"`
	ClaudeHome     string `json:"claude_home,omitempty"`
	ClaudeRegistry string `json:"claude_registry,omitempty"`
	CodexHome      string `json:"codex_home,omitempty"`
}

var runWSL = func(args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	child := exec.CommandContext(ctx, "wsl.exe", args...)
	// Inno/Repair capture progress; discovery must not allocate a console.
	hideWSLConsole(child)
	var stderr strings.Builder
	child.Stderr = &stderr
	data, err := child.Output()
	if ctx.Err() != nil {
		return "", fmt.Errorf("WSL command timed out")
	}
	if err != nil {
		// wsl.exe reports its own failures (unknown distribution, WSL not
		// installed) on stdout; Linux commands report theirs on stderr.
		detail := strings.TrimSpace(stderr.String())
		if detail == "" {
			detail = strings.Join(strings.Fields(decodeWSL(data)), " ")
		}
		return "", fmt.Errorf("%w: %s", err, detail)
	}
	return decodeWSL(data), nil
}

func decodeWSL(data []byte) string {
	// Windows wsl --list emits UTF-16LE; commands inside Linux emit UTF-8.
	if len(data) >= 2 && (data[1] == 0 || (data[0] == 0xff && data[1] == 0xfe)) {
		units := make([]uint16, 0, len(data)/2)
		for i := 0; i+1 < len(data); i += 2 {
			units = append(units, uint16(data[i])|uint16(data[i+1])<<8)
		}
		return strings.TrimPrefix(string(utf16.Decode(units)), "\ufeff")
	}
	return string(data)
}

func validDistro(name string) bool {
	return name != "" && name != "." && name != ".." && !strings.ContainsAny(name, "\\/:|\r\n\x00")
}

func wslArgs(distro, user string, command ...string) []string {
	args := []string{"--distribution", distro}
	if user != "" {
		args = append(args, "--user", user)
	}
	return append(append(args, "--exec"), command...)
}

const wslProbe = `set -eu
home=${1:-$HOME}
claude=${2:-${CLAUDE_CONFIG_DIR:-$home/.claude}}
codex=${3:-${CODEX_HOME:-$home/.codex}}
printf '%s\n' "$home"
if [ -d "$claude" ]; then printf '%s\n' "$claude"; else printf '\n'; fi
if [ -d "$codex" ]; then printf '%s\n' "$codex"; else printf '\n'; fi
registry=$home/.claude.json
if [ -n "${2:-${CLAUDE_CONFIG_DIR:-}}" ]; then registry=$claude/.claude.json; fi
printf '%s\n' "$registry"
`

func probeWSL(distro, user, home, claude, codex string) (wslProfile, error) {
	if !validDistro(distro) {
		return wslProfile{}, fmt.Errorf("invalid WSL distribution")
	}
	value, err := runWSL(wslArgs(distro, user, "sh", "-c", wslProbe, "imprint-probe", home, claude, codex)...)
	if err != nil {
		return wslProfile{}, err
	}
	lines := strings.Split(strings.ReplaceAll(value, "\r\n", "\n"), "\n")
	if len(lines) < 4 || !validLinuxPath(lines[0]) {
		return wslProfile{}, fmt.Errorf("invalid WSL profile response")
	}
	p := wslProfile{Distribution: distro, Home: lines[0], ClaudeHome: lines[1], CodexHome: lines[2], ClaudeRegistry: lines[3]}
	for _, dir := range []string{p.ClaudeHome, p.CodexHome, p.ClaudeRegistry} {
		if dir != "" && !validLinuxPath(dir) {
			return wslProfile{}, fmt.Errorf("invalid WSL client path")
		}
	}
	return p, nil
}
func validLinuxPath(value string) bool {
	return strings.HasPrefix(value, "/") && !strings.ContainsAny(value, "\\\r\n\x00")
}

func listWSL() []wslProfile {
	result := []wslProfile{}
	text, err := runWSL("--list", "--quiet")
	if err != nil {
		fmt.Fprintln(os.Stderr, "WSL discovery unavailable:", err)
		return result
	}
	for _, name := range strings.Split(text, "\n") {
		name = strings.TrimSpace(name)
		if !validDistro(name) || strings.HasPrefix(strings.ToLower(name), "docker-desktop") || strings.HasPrefix(strings.ToLower(name), "rancher-desktop") {
			continue
		}
		p, err := probeWSL(name, "", "", "", "")
		if err != nil {
			fmt.Fprintf(os.Stderr, "Skipping WSL distribution %s: %v\n", name, err)
			continue
		}
		result = append(result, p)
	}
	return result
}

func shellQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "'\"'\"'") + "'" }
func wslUNC(distro, linuxPath string) string {
	return `\\wsl.localhost\` + distro + strings.ReplaceAll(path.Clean(linuxPath), "/", `\`)
}

func wslClaudeRegistry(p wslProfile) string {
	if p.ClaudeRegistry != "" {
		return p.ClaudeRegistry
	}
	if p.ClaudeHome != path.Join(p.Home, ".claude") {
		return path.Join(p.ClaudeHome, ".claude.json")
	}
	return path.Join(p.Home, ".claude.json")
}

// wslDriveMounts prints the distribution's Windows drive mounts from its
// kernel mount table, one "<drive path>\t<mount point>" line each: the table
// imprint.wsl_paths maps drive paths with (ADR 0026).
const wslDriveMounts = "while read -r source target kind rest; do\n" +
	"  case \"$source\" in [a-zA-Z]:*) case \"$kind\" in drvfs|9p) printf '%s\\t%s\\n' \"$source\" \"$target\";; esac;; esac\n" +
	"done < /proc/mounts"

func wslLauncher(p wslProfile, bp backendPaths) string {
	// wslpath honors custom Windows drive mount locations. No shell re-parses
	// client payloads: stdin goes directly to the stdlib Windows bridge.
	cli, _ := os.Executable()
	return "#!/bin/sh\n# Imprint managed Windows runtime bridge (imprint-wsl).\nset -eu\n" +
		"python=$(wslpath -u " + shellQuote(bp.VenvPython) + ")\n" +
		"mounts=$(" + wslDriveMounts + ")\n" +
		"exec \"$python\" " + shellQuote(filepath.Join(bp.ProjectDir, "imprint", "wsl_bridge.py")) +
		" --cli " + shellQuote(cli) + " --data-dir " + shellQuote(bp.DataDir) + " --distribution " + shellQuote(p.Distribution) +
		" --cwd \"$PWD\" --mounts \"$mounts\" --home " + shellQuote(p.Home) + " \"$@\"\n"
}

func wslProfileMapper(p wslProfile, user string) (func(string) string, error) {
	type root struct{ linux, windows string }
	roots := []root{}
	for _, value := range []string{p.ClaudeHome, p.CodexHome, p.Home} {
		if value == "" {
			continue
		}
		converted, err := runWSL(wslArgs(p.Distribution, user, "wslpath", "-w", value)...)
		if err != nil {
			return nil, err
		}
		converted = strings.TrimSpace(converted)
		if !filepath.IsAbs(converted) {
			return nil, fmt.Errorf("WSL returned a nonabsolute Windows profile path")
		}
		roots = append(roots, root{path.Clean(value), converted})
	}
	return func(value string) string {
		best := -1
		for i, r := range roots {
			if (value == r.linux || strings.HasPrefix(value, r.linux+"/")) && (best < 0 || len(r.linux) > len(roots[best].linux)) {
				best = i
			}
		}
		if best >= 0 {
			r := roots[best]
			return filepath.Join(r.windows, filepath.FromSlash(strings.TrimPrefix(strings.TrimPrefix(value, r.linux), "/")))
		}
		return wslUNC(p.Distribution, value)
	}, nil
}

// applyWSLProfile takes a filesystem mapper so fixtures never use real homes.
func applyWSLProfile(p wslProfile, bp backendPaths, target string, local func(string) string, makeExecutable func(string) error) error {
	var dir string
	switch target {
	case "codex":
		dir = p.CodexHome
	case "claude-code":
		dir = p.ClaudeHome
	default:
		return fmt.Errorf("target must be claude-code or codex")
	}
	if dir == "" {
		return fmt.Errorf("%s profile not detected in %s", target, p.Distribution)
	}
	launcher := path.Join(dir, "imprint-wsl")
	if previous, err := os.ReadFile(local(launcher)); err == nil && !strings.Contains(string(previous), "# Imprint managed Windows runtime bridge") {
		return fmt.Errorf("refusing to overwrite an unrecognized launcher: %s", launcher)
	} else if err != nil && !os.IsNotExist(err) {
		return err
	}
	if err := configfile.Write(local(launcher), []byte(wslLauncher(p, bp))); err != nil {
		return err
	}
	if err := makeExecutable(launcher); err != nil {
		return err
	}
	spec := map[string]any{"command": launcher, "args": []any{"mcp"}, "env": map[string]any{}}
	var rules, hooksFile, rule string
	if target == "codex" {
		if _, err := tomlutil.EnsureMCPServer(local(path.Join(dir, "config.toml")), "imprint", spec); err != nil {
			return err
		}
		rules = path.Join(dir, "AGENTS.md")
		hooksFile = path.Join(dir, "hooks.json")
		rule = instructions.CodexRule
	} else {
		// Claude's user-scoped MCP registry is beside its config directory by
		// default; CLAUDE_CONFIG_DIR moves the registry into that directory.
		registry := wslClaudeRegistry(p)
		if _, err := jsonutil.EnsureMCPServer(local(registry), "imprint", spec); err != nil {
			return err
		}
		rules = path.Join(dir, "CLAUDE.md")
		hooksFile = path.Join(dir, "settings.json")
		rule = instructions.ClaudeCodeCLAUDE
		if _, err := jsonutil.EnsurePermission(local(hooksFile), "mcp__imprint__*"); err != nil {
			return err
		}
	}
	existing, err := os.ReadFile(local(rules))
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	managed := instructions.MarkerStart + "\n" + rule + instructions.MarkerEnd + "\n"
	if err := configfile.Write(local(rules), []byte(instructions.MergeManaged(string(existing), managed))); err != nil {
		return err
	}
	type hookSpec struct {
		event, matcher, action string
		timeout                int
		async                  bool
	}
	entries := []hookSpec{
		{"SessionStart", "startup|resume", "session-start", 10, false}, {"Stop", "", "stop", 120, true},
	}
	if target == "claude-code" {
		entries = append(entries,
			hookSpec{"PreCompact", "", "pre-compact", 90, false},
			hookSpec{"PostToolUse", "mcp__imprint__search", "post-search", 10, true},
			hookSpec{"PreToolUse", "Read|Grep|Glob", "pre-read", 10, false})
	}
	for _, h := range entries {
		command := shellQuote(launcher) + " hook " + h.action
		if err := jsonutil.SetOwnedHookWithMatcher(local(hooksFile), h.event, h.matcher, command, h.timeout, h.async); err != nil {
			return err
		}
	}
	return nil
}

func removeWSLProfile(p wslProfile, target string, local func(string) string) error {
	dir, registry, rules, hooksFile := p.CodexHome, "", "AGENTS.md", "hooks.json"
	if target == "claude-code" {
		dir, rules, hooksFile = p.ClaudeHome, "CLAUDE.md", "settings.json"
		registry = wslClaudeRegistry(p)
	} else if target != "codex" {
		return fmt.Errorf("target must be claude-code or codex")
	}
	if dir == "" {
		return fmt.Errorf("client profile was not found")
	}
	launcher := path.Join(dir, "imprint-wsl")
	previous, err := os.ReadFile(local(launcher))
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	if err == nil && !strings.Contains(string(previous), "# Imprint managed Windows runtime bridge") {
		return fmt.Errorf("unrecognized launcher; refusing removal")
	}
	var data map[string]any
	if target == "codex" {
		registry = path.Join(dir, "config.toml")
		content, readErr := os.ReadFile(local(registry))
		if readErr != nil && !os.IsNotExist(readErr) {
			return readErr
		}
		if err := toml.Unmarshal(content, &data); err != nil {
			return err
		}
	} else {
		data, err = jsonutil.ReadJSON(local(registry))
		if err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	rootKey := "mcp_servers"
	if target == "claude-code" {
		rootKey = "mcpServers"
	}
	if servers, ok := data[rootKey].(map[string]any); ok {
		if entry, ok := servers["imprint"].(map[string]any); ok && entry["command"] != launcher {
			return fmt.Errorf("imprint now points to another runtime; refusing to remove it")
		}
	}
	if target == "codex" {
		_, err = tomlutil.RemoveMCPServer(local(registry), "imprint")
	} else {
		_, err = jsonutil.RemoveMCPServer(local(registry), "mcpServers", "imprint")
	}
	if err != nil {
		return err
	}
	if _, err := jsonutil.RemoveHooksMatching(local(path.Join(dir, hooksFile)), []string{shellQuote(launcher) + " hook "}); err != nil && !os.IsNotExist(err) {
		return err
	}
	if content, err := os.ReadFile(local(path.Join(dir, rules))); err == nil {
		if strings.Contains(string(content), instructions.MarkerStart) {
			if err := configfile.Write(local(path.Join(dir, rules)), []byte(instructions.MergeManaged(string(content), ""))); err != nil {
				return err
			}
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	if target == "claude-code" {
		if err := removePermission(local(path.Join(dir, hooksFile)), "mcp__imprint__*"); err != nil {
			return err
		}
	}
	if err := os.Remove(local(launcher)); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func WSL(args []string) {
	if runtime.GOOS != "windows" {
		output.Fail("Run imprint wsl from the Windows installation")
	}
	if len(args) == 0 {
		output.Fail("Usage: imprint wsl list | setup --distribution NAME --target claude-code|codex")
	}
	flags := flag.NewFlagSet("wsl", flag.ContinueOnError)
	distro := flags.String("distribution", "", "WSL distribution")
	user := flags.String("user", "", "WSL Linux user (default: distro default)")
	target := flags.String("target", "", "claude-code or codex")
	home := flags.String("home", "", "Explicit Linux home (disposable profiles or nondefault configuration)")
	claude := flags.String("claude-home", "", "Explicit CLAUDE_CONFIG_DIR")
	codex := flags.String("codex-home", "", "Explicit CODEX_HOME")
	python := flags.String("python", "", "Explicit Windows Imprint interpreter (default: installed venv)")
	installerFile := flags.String("installer-file", "", "Write installer discovery rows to this file")
	if err := flags.Parse(args[1:]); err != nil {
		output.Fail(err.Error())
	}
	if flags.NArg() != 0 {
		output.Fail("Unexpected WSL arguments")
	}
	switch args[0] {
	case "list":
		profiles := listWSL()
		if *installerFile != "" {
			var rows strings.Builder
			for _, p := range profiles {
				if p.ClaudeHome != "" {
					fmt.Fprintf(&rows, "%s|claude-code\n", p.Distribution)
				}
				if p.CodexHome != "" {
					fmt.Fprintf(&rows, "%s|codex\n", p.Distribution)
				}
			}
			if err := os.WriteFile(*installerFile, []byte(rows.String()), 0600); err != nil {
				output.Fail(err.Error())
			}
		} else {
			_ = json.NewEncoder(os.Stdout).Encode(profiles)
		}
	case "setup", "remove":
		p, err := probeWSL(*distro, *user, *home, *claude, *codex)
		if err != nil {
			output.Fail(err.Error())
		}
		project := platform.FindProjectDir()
		local, err := wslProfileMapper(p, *user)
		if err != nil {
			output.Fail(err.Error())
		}
		if args[0] == "remove" {
			if err := removeWSLProfile(p, *target, local); err != nil {
				output.Fail(err.Error())
			}
			if err := saveWSLRegistration(project, wslRegistration{Profile: p, User: *user, Target: *target, CustomProfile: *home != "" || *claude != "" || *codex != ""}, true); err != nil {
				output.Fail(err.Error())
			}
			// Only default-user registrations are represented in installer choices.
			if *user == "" && *home == "" && *claude == "" && *codex == "" {
				selectionFile := filepath.Join(project, ".wsl-integrations.txt")
				if content, err := os.ReadFile(selectionFile); err == nil {
					kept := []string{}
					for _, line := range strings.Split(strings.TrimSpace(strings.TrimPrefix(string(content), "\ufeff")), "\n") {
						if strings.TrimSpace(line) != p.Distribution+"|"+*target {
							kept = append(kept, line)
						}
					}
					if err := configfile.Write(selectionFile, []byte(strings.Join(kept, "\n")+"\n")); err != nil {
						output.Fail(err.Error())
					}
				} else if !os.IsNotExist(err) {
					output.Fail(err.Error())
				}
			}
			output.Success("Removed the selected WSL bridge; memories and other integrations are preserved")
			return
		}
		bp := backendPaths{ProjectDir: project, VenvPython: platform.VenvPython(project), DataDir: platform.DataDir(project)}
		if *python != "" {
			bp.VenvPython = *python
		}
		if !platform.FileExists(bp.VenvPython) {
			output.Fail("Windows runtime missing; run imprint bootstrap first")
		}
		// Journal before mutation so even partial setup is removable.
		if *target != "claude-code" && *target != "codex" {
			output.Fail("target must be claude-code or codex")
		}
		if err := saveWSLRegistration(project, wslRegistration{Profile: p, User: *user, Target: *target, CustomProfile: *home != "" || *claude != "" || *codex != ""}, false); err != nil {
			output.Fail(err.Error())
		}
		err = applyWSLProfile(p, bp, *target, local, func(value string) error {
			_, err := runWSL(wslArgs(p.Distribution, *user, "chmod", "700", value)...)
			if err == nil {
				_, err = runWSL(wslArgs(p.Distribution, *user, value, "probe")...)
			}
			return err
		})
		if err != nil {
			output.Fail("WSL registration failed: " + err.Error())
		}
		output.Success(fmt.Sprintf("Registered Imprint for %s in WSL %s. Restart the client and review/trust its hooks.", *target, p.Distribution))
	default:
		output.Fail("Unknown WSL command: " + args[0])
	}
}
