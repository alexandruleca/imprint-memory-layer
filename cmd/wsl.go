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
	"slices"
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
	CursorHome     string `json:"cursor_home,omitempty"`
	ClineHome      string `json:"cline_home,omitempty"`
}

// The clients a WSL profile can be registered for, and where each keeps its
// profile, are the clients table's wslHome entries (wslTargets, wslClientDir).

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
if [ -d "$home/.cursor" ]; then printf '%s\n' "$home/.cursor"; else printf '\n'; fi
cline=${CLINE_DIR:-$home/.cline}
if [ -d "$cline" ]; then printf '%s\n' "$cline"; else printf '\n'; fi
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
	if len(lines) < 6 || !validLinuxPath(lines[0]) {
		return wslProfile{}, fmt.Errorf("invalid WSL profile response")
	}
	p := wslProfile{Distribution: distro, Home: lines[0], ClaudeHome: lines[1], CodexHome: lines[2], ClaudeRegistry: lines[3], CursorHome: lines[4], ClineHome: lines[5]}
	for _, dir := range []string{p.ClaudeHome, p.CodexHome, p.ClaudeRegistry, p.CursorHome, p.ClineHome} {
		if dir != "" && !validLinuxPath(dir) {
			return wslProfile{}, fmt.Errorf("invalid WSL client path")
		}
	}
	return p, nil
}
func validLinuxPath(value string) bool {
	return strings.HasPrefix(value, "/") && !strings.ContainsAny(value, "\\\r\n\x00")
}

// wslListing is what `imprint wsl list` reports: each distribution's default
// user profile and, for every WSL target, the client's directory there and
// whether this installation enrolled it.
type wslListing struct {
	Targets       []wslTargetInfo   `json:"targets"`
	Distributions []wslDistribution `json:"distributions"`
	Error         string            `json:"error,omitempty"` // discovery itself failed
}

type wslTargetInfo struct {
	Name  string `json:"name"`
	Label string `json:"label"`
}

type wslDistribution struct {
	Distribution string      `json:"distribution"`
	Home         string      `json:"home,omitempty"`
	Error        string      `json:"error,omitempty"` // the profile probe failed
	Clients      []wslClient `json:"clients"`
}

type wslClient struct {
	Name     string `json:"name"`
	Profile  string `json:"profile"` // "" when the client has no profile there
	Enrolled bool   `json:"enrolled"`
}

// listWSL probes every distribution except Docker/Rancher utility ones, which
// can start a stopped distribution (ADR 0026). Enrollment is this
// installation's journal of default-user registrations plus the installer's
// saved selections; custom users and homes are managed from the CLI.
func listWSL(project string) wslListing {
	listing := wslListing{Targets: []wslTargetInfo{}, Distributions: []wslDistribution{}}
	for _, c := range clients {
		if c.wslHome != nil {
			listing.Targets = append(listing.Targets, wslTargetInfo{Name: c.name, Label: c.label})
		}
	}
	enrolled, err := enrolledWSL(project)
	if err != nil {
		listing.Error = err.Error()
		return listing
	}
	text, err := runWSL("--list", "--quiet")
	if err != nil {
		listing.Error = "WSL discovery unavailable: " + err.Error()
		return listing
	}
	for _, name := range strings.Split(text, "\n") {
		name = strings.TrimSpace(name)
		if !validDistro(name) || strings.HasPrefix(strings.ToLower(name), "docker-desktop") || strings.HasPrefix(strings.ToLower(name), "rancher-desktop") {
			continue
		}
		d := wslDistribution{Distribution: name, Clients: []wslClient{}}
		p, err := probeWSL(name, "", "", "", "")
		if err != nil {
			d.Error = err.Error()
		} else {
			d.Home = p.Home
		}
		for _, c := range clients {
			if c.wslHome == nil {
				continue
			}
			state := wslClient{Name: c.name, Enrolled: enrolled[name+"|"+c.name]}
			if err == nil {
				state.Profile = c.wslHome(p)
			}
			d.Clients = append(d.Clients, state)
		}
		listing.Distributions = append(listing.Distributions, d)
	}
	return listing
}

// enrolledWSL is the set of "distribution|target" default-user profiles this
// installation registered.
func enrolledWSL(project string) (map[string]bool, error) {
	records, err := readWSLRegistrations(project)
	if err != nil {
		return nil, err
	}
	enrolled := map[string]bool{}
	for _, r := range records {
		if r.User == "" && !r.CustomProfile {
			enrolled[r.Profile.Distribution+"|"+r.Target] = true
		}
	}
	selections, err := os.ReadFile(filepath.Join(project, ".wsl-integrations.txt"))
	if err != nil && !os.IsNotExist(err) {
		return nil, err
	}
	for _, line := range strings.Split(strings.TrimPrefix(string(selections), "\ufeff"), "\n") {
		if line = strings.TrimSpace(line); line != "" {
			enrolled[line] = true
		}
	}
	return enrolled, nil
}

func printWSLListing(listing wslListing) {
	if listing.Error != "" {
		output.Fail(listing.Error)
	}
	if len(listing.Distributions) == 0 {
		output.Info("No WSL distributions found.")
		return
	}
	labels := map[string]string{}
	for _, t := range listing.Targets {
		labels[t.Name] = t.Label
	}
	for _, d := range listing.Distributions {
		if d.Error != "" {
			fmt.Printf("  %s: %s\n", d.Distribution, d.Error)
			continue
		}
		fmt.Printf("  %s (%s)\n", d.Distribution, d.Home)
		for _, c := range d.Clients {
			state := "no profile"
			if c.Enrolled {
				state = "enrolled"
			} else if c.Profile != "" {
				state = "not enrolled"
			}
			fmt.Printf("    %-16s %-14s %s\n", labels[c.Name], state, c.Profile)
		}
	}
	fmt.Println()
	fmt.Println("  Enroll a profile with imprint wsl setup --distribution NAME --target CLIENT; remove it with imprint wsl remove.")
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
	for _, value := range []string{p.ClaudeHome, p.CodexHome, p.ClineHome, p.Home} {
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

// wslHook is a lifecycle hook the bridge launcher answers.
type wslHook struct {
	event, matcher, action string
	timeout                int
	async                  bool
}

// wslLayout is what imprint wsl setup writes into a target's WSL profile and
// imprint wsl remove takes out again. A layout without a registry writes
// nothing: Cursor's registration only lets learn read its store.
type wslLayout struct {
	registry, rootKey string // MCP server registry and its server table
	toml              bool
	spec              map[string]any
	rules             string // instructions file
	rule              string // Imprint's text: the whole file when ownRules, else a managed block
	ownRules          bool
	hooks             string // Claude-style hooks file; "" for a client without hooks
	hookSet           []wslHook
	permission        bool // Claude Code's permission for Imprint's tools
}

func wslLayoutOf(p wslProfile, target, dir string) wslLayout {
	launcher := path.Join(dir, "imprint-wsl")
	stdio := map[string]any{"command": launcher, "args": []any{"mcp"}, "env": map[string]any{}}
	lifecycle := []wslHook{{"SessionStart", "startup|resume", "session-start", 10, false}, {"Stop", "", "stop", 120, true}}
	switch target {
	case "claude-code":
		// Claude's user-scoped MCP registry is beside its config directory by
		// default; CLAUDE_CONFIG_DIR moves the registry into that directory.
		return wslLayout{registry: wslClaudeRegistry(p), rootKey: "mcpServers", spec: stdio,
			rules: path.Join(dir, "CLAUDE.md"), rule: instructions.ClaudeCodeCLAUDE,
			hooks: path.Join(dir, "settings.json"), permission: true, hookSet: append(lifecycle,
				wslHook{"PreCompact", "", "pre-compact", 90, false},
				wslHook{"PostToolUse", "mcp__imprint__search", "post-search", 10, true},
				wslHook{"PreToolUse", "Read|Grep|Glob", "pre-read", 10, false})}
	case "codex":
		return wslLayout{registry: path.Join(dir, "config.toml"), rootKey: "mcp_servers", toml: true, spec: stdio,
			rules: path.Join(dir, "AGENTS.md"), rule: instructions.CodexRule, hooks: path.Join(dir, "hooks.json"), hookSet: lifecycle}
	case "cline":
		// Cline's data and rules under its directory; its hooks receive no
		// transcript, so it gets none (ADR 0056).
		return wslLayout{registry: path.Join(dir, "data", "settings", "cline_mcp_settings.json"), rootKey: "mcpServers",
			spec:  clineServer(launcher, []any{"mcp"}, map[string]any{}),
			rules: path.Join(dir, "rules", "imprint.md"), rule: instructions.ClineRule, ownRules: true}
	}
	return wslLayout{}
}

// applyWSLProfile takes a filesystem mapper so fixtures never use real homes.
func applyWSLProfile(p wslProfile, bp backendPaths, target string, local func(string) string, makeExecutable func(string) error) error {
	dir, err := wslClientDir(p, target)
	if err != nil {
		return err
	}
	if dir == "" {
		return fmt.Errorf("%s profile not detected in %s", target, p.Distribution)
	}
	layout := wslLayoutOf(p, target, dir)
	if layout.registry == "" {
		return nil
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
	if layout.toml {
		_, err = tomlutil.EnsureMCPServer(local(layout.registry), "imprint", layout.spec)
	} else {
		_, err = jsonutil.EnsureMCPServerAtKey(local(layout.registry), layout.rootKey, "imprint", layout.spec)
	}
	if err != nil {
		return err
	}
	if layout.permission {
		if _, err := jsonutil.EnsurePermission(local(layout.hooks), "mcp__imprint__*"); err != nil {
			return err
		}
	}
	if layout.ownRules {
		if err := configfile.Write(local(layout.rules), []byte(layout.rule)); err != nil {
			return err
		}
	} else {
		existing, err := os.ReadFile(local(layout.rules))
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		managed := instructions.MarkerStart + "\n" + layout.rule + instructions.MarkerEnd + "\n"
		if err := configfile.Write(local(layout.rules), []byte(instructions.MergeManaged(string(existing), managed))); err != nil {
			return err
		}
	}
	for _, h := range layout.hookSet {
		command := shellQuote(launcher) + " hook " + h.action
		if err := jsonutil.SetOwnedHookWithMatcher(local(layout.hooks), h.event, h.matcher, command, h.timeout, h.async); err != nil {
			return err
		}
	}
	return nil
}

func removeWSLProfile(p wslProfile, target string, local func(string) string) error {
	dir, err := wslClientDir(p, target)
	if err != nil {
		return err
	}
	if dir == "" {
		return fmt.Errorf("client profile was not found")
	}
	layout := wslLayoutOf(p, target, dir)
	if layout.registry == "" {
		return nil
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
	if layout.toml {
		content, readErr := os.ReadFile(local(layout.registry))
		if readErr != nil && !os.IsNotExist(readErr) {
			return readErr
		}
		if err := toml.Unmarshal(content, &data); err != nil {
			return err
		}
	} else {
		data, err = jsonutil.ReadJSON(local(layout.registry))
		if err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	if servers, ok := data[layout.rootKey].(map[string]any); ok {
		if entry, ok := servers["imprint"].(map[string]any); ok && entry["command"] != launcher {
			return fmt.Errorf("imprint now points to another runtime; refusing to remove it")
		}
	}
	if layout.toml {
		_, err = tomlutil.RemoveMCPServer(local(layout.registry), "imprint")
	} else {
		_, err = jsonutil.RemoveMCPServer(local(layout.registry), layout.rootKey, "imprint")
	}
	if err != nil {
		return err
	}
	if layout.hooks != "" {
		if _, err := jsonutil.RemoveHooksMatching(local(layout.hooks), []string{shellQuote(launcher) + " hook "}); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	if content, err := os.ReadFile(local(layout.rules)); err == nil {
		if layout.ownRules {
			if string(content) == layout.rule {
				if err := os.Remove(local(layout.rules)); err != nil {
					return err
				}
			}
		} else if strings.Contains(string(content), instructions.MarkerStart) {
			if err := configfile.Write(local(layout.rules), []byte(instructions.MergeManaged(string(content), ""))); err != nil {
				return err
			}
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	if layout.permission {
		if err := removePermission(local(layout.hooks), "mcp__imprint__*"); err != nil {
			return err
		}
	}
	if err := os.Remove(local(launcher)); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

// wslClineSessions is where Cline keeps sessions in a WSL profile: the
// default place under its directory, since the profile's environment
// overrides are not read (ADR 0026).
func wslClineSessions(p wslProfile) string {
	if p.ClineHome == "" {
		return ""
	}
	return path.Join(p.ClineHome, "data", "sessions")
}

func WSL(args []string) {
	if runtime.GOOS != "windows" {
		output.Fail("Run imprint wsl from the Windows installation")
	}
	if len(args) == 0 {
		output.Fail("Usage: imprint wsl list [--json] | setup|remove --distribution NAME --target " + WSLTargets())
	}
	flags := flag.NewFlagSet("wsl", flag.ContinueOnError)
	distro := flags.String("distribution", "", "WSL distribution")
	user := flags.String("user", "", "WSL Linux user (default: distro default)")
	target := flags.String("target", "", wslTargetChoices())
	home := flags.String("home", "", "Explicit Linux home (disposable profiles or nondefault configuration)")
	claude := flags.String("claude-home", "", "Explicit CLAUDE_CONFIG_DIR")
	codex := flags.String("codex-home", "", "Explicit CODEX_HOME")
	python := flags.String("python", "", "Explicit Windows Imprint interpreter (default: installed venv)")
	installerFile := flags.String("installer-file", "", "Write installer discovery rows to this file")
	asJSON := flags.Bool("json", false, "Print the listing as JSON (what the dashboard reads)")
	if err := flags.Parse(args[1:]); err != nil {
		output.Fail(err.Error())
	}
	if flags.NArg() != 0 {
		output.Fail("Unexpected WSL arguments")
	}
	switch args[0] {
	case "list":
		listing := listWSL(platform.FindProjectDir())
		switch {
		case *installerFile != "":
			if listing.Error != "" {
				fmt.Fprintln(os.Stderr, listing.Error)
			}
			var rows strings.Builder
			for _, d := range listing.Distributions {
				if d.Error != "" {
					fmt.Fprintf(os.Stderr, "Skipping WSL distribution %s: %s\n", d.Distribution, d.Error)
				}
				for _, c := range d.Clients {
					if c.Profile != "" {
						fmt.Fprintf(&rows, "%s|%s\n", d.Distribution, c.Name)
					}
				}
			}
			if err := os.WriteFile(*installerFile, []byte(rows.String()), 0600); err != nil {
				output.Fail(err.Error())
			}
		case *asJSON:
			_ = json.NewEncoder(os.Stdout).Encode(listing)
		default:
			printWSLListing(listing)
		}
	case "setup", "remove":
		if !slices.Contains(wslTargets(), *target) {
			output.Fail("target must be " + wslTargetChoices())
		}
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
		if *target == "cursor" {
			output.Success(fmt.Sprintf("Registered Cursor's conversations in WSL %s for imprint learn. Its MCP server and hooks come from Imprint's Windows Cursor setup.", p.Distribution))
			return
		}
		next := "Restart the client and review/trust its hooks."
		if dir, _ := wslClientDir(p, *target); wslLayoutOf(p, *target, dir).hooks == "" {
			next = "Restart the client."
		}
		output.Success(fmt.Sprintf("Registered Imprint for %s in WSL %s. %s", *target, p.Distribution, next))
	default:
		output.Fail("Unknown WSL command: " + args[0])
	}
}
