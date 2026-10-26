package cmd

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

var workspaceNameRE = regexp.MustCompile(`^[a-z0-9][a-z0-9-]*$`)

type workspaceConfig struct {
	Active string   `json:"active"`
	Known  []string `json:"known"`
}

func readWorkspaceConfig(dataDir string) workspaceConfig {
	path := filepath.Join(dataDir, "workspace.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return workspaceConfig{Active: "default", Known: []string{"default"}}
	}
	var cfg workspaceConfig
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return workspaceConfig{Active: "default", Known: []string{"default"}}
	}
	if cfg.Active == "" {
		cfg.Active = "default"
	}
	if len(cfg.Known) == 0 {
		cfg.Known = []string{"default"}
	}
	return cfg
}

func writeWorkspaceConfig(dataDir string, cfg workspaceConfig) error {
	path := filepath.Join(dataDir, "workspace.json")
	raw, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(raw, '\n'), 0644)
}

func validateWorkspaceName(name string) error {
	if name == "" {
		return fmt.Errorf("name cannot be empty")
	}
	if len(name) > 40 {
		return fmt.Errorf("name too long (max 40)")
	}
	if !workspaceNameRE.MatchString(name) {
		return fmt.Errorf("must be lowercase alphanumeric + hyphens, start with letter/digit")
	}
	return nil
}

func collectionName(workspace string) string {
	if workspace == "default" {
		return "memories"
	}
	return "memories_" + workspace
}

// Workspace dispatches workspace subcommands.
//
//	imprint workspace              — list
//	imprint workspace list         — list
//	imprint workspace switch <n>   — switch (create if new)
//	imprint workspace <n>          — shortcut for switch
//	imprint workspace delete <n>   — delete workspace data
//	imprint workspace use [<n>]    — this folder's workspace (ADR 0072)
func Workspace(args []string) {
	projectDir := platform.FindProjectDir()
	dataDir := platform.DataDir(projectDir)

	if len(args) == 0 {
		workspaceList(dataDir)
		return
	}

	switch args[0] {
	case "map":
		runWorkspacePolicy(append([]string{"map"}, args[1:]...), false)
	case "roots", "exclusions":
		action := args[0]
		tail := args[1:]
		if len(tail) > 0 && (tail[0] == "add" || tail[0] == "remove" || tail[0] == "restore") {
			action, tail = tail[0], tail[1:]
		}
		runWorkspacePolicy(append([]string{action}, tail...), false)
	case "list":
		workspaceList(dataDir)
	case "use":
		workspaceUse(projectDir, dataDir, args[1:])
	case "switch":
		if len(args) < 2 {
			output.Fail("usage: imprint workspace switch <name>")
		}
		workspaceSwitch(dataDir, args[1])
	case "delete":
		if len(args) < 2 {
			output.Fail("usage: imprint workspace delete <name>")
		}
		workspaceDelete(projectDir, dataDir, args[1])
	default:
		// Bare argument = shortcut for switch
		workspaceSwitch(dataDir, args[0])
	}
}

func workspaceList(dataDir string) {
	cfg := readWorkspaceConfig(dataDir)
	fmt.Println()
	output.Header("Workspaces")
	fmt.Println()
	for _, ws := range cfg.Known {
		if ws == cfg.Active {
			fmt.Printf("  %s (active)\n", ws)
		} else {
			fmt.Printf("  %s\n", ws)
		}
	}
	fmt.Println()
}

func workspaceSwitch(dataDir string, name string) {
	if err := validateWorkspaceName(name); err != nil {
		output.Fail("invalid workspace name: " + err.Error())
	}

	cfg := readWorkspaceConfig(dataDir)
	cfg.Active = name

	// Add to known if new
	found := false
	for _, ws := range cfg.Known {
		if ws == name {
			found = true
			break
		}
	}
	if !found {
		cfg.Known = append(cfg.Known, name)
	}

	if err := writeWorkspaceConfig(dataDir, cfg); err != nil {
		output.Fail("failed to write config: " + err.Error())
	}

	if found {
		output.Success("switched to workspace: " + name)
	} else {
		output.Success("created and switched to workspace: " + name)
	}
}

func workspaceDelete(projectDir, dataDir, name string) {
	if name == "default" {
		output.Fail("cannot delete the default workspace — use 'imprint wipe' instead")
	}

	cfg := readWorkspaceConfig(dataDir)
	if name == cfg.Active {
		output.Fail("cannot delete the active workspace — switch to a different workspace first")
	}

	// Check if workspace is known
	known := false
	for _, ws := range cfg.Known {
		if ws == name {
			known = true
			break
		}
	}
	if !known {
		output.Fail("workspace not found: " + name)
	}

	coll := collectionName(name)

	// Delete Qdrant collection via Python
	venvPython := platform.VenvPython(projectDir)
	if platform.FileExists(venvPython) {
		envVars := []string{
			"PYTHONPATH=" + projectDir,
			"IMPRINT_DATA_DIR=" + dataDir,
		}
		output.Info("deleting collection: " + coll)
		out, err := runner.RunCaptureEnv(venvPython, envVars,
			"-c", fmt.Sprintf(`
from imprint import qdrant_runner
from qdrant_client import QdrantClient
h, p = qdrant_runner.ensure_running()
c = QdrantClient(host=h, port=p, timeout=10)
try:
    c.delete_collection(%q)
    print("deleted")
except Exception as e:
    print(f"skip: {e}")
`, coll))
		if err != nil {
			output.Warn("collection delete failed: " + err.Error())
		} else {
			out = strings.TrimSpace(out)
			if out == "deleted" {
				output.Success("removed collection " + coll)
			} else {
				output.Info(out)
			}
		}
	}

	// Delete SQLite DB files
	for _, suffix := range []string{"", "-shm", "-wal"} {
		path := filepath.Join(dataDir, fmt.Sprintf("imprint_graph_%s.sqlite3%s", name, suffix))
		if platform.FileExists(path) {
			if err := os.Remove(path); err != nil {
				output.Warn("failed to remove " + path + ": " + err.Error())
			} else {
				output.Success("removed " + filepath.Base(path))
			}
		}
	}

	// Delete WAL file
	walPath := filepath.Join(dataDir, fmt.Sprintf("wal_%s.jsonl", name))
	if platform.FileExists(walPath) {
		if err := os.Remove(walPath); err != nil {
			output.Warn("failed to remove WAL: " + err.Error())
		} else {
			output.Success("removed " + filepath.Base(walPath))
		}
	}

	if platform.FileExists(venvPython) {
		_, err := runner.RunCaptureEnv(venvPython, []string{"PYTHONPATH=" + projectDir, "IMPRINT_DATA_DIR=" + dataDir},
			"-c", fmt.Sprintf("from imprint.workspace_policy import remove_workspace; remove_workspace(%q)", name))
		if err != nil {
			output.Fail("Could not remove workspace policy: " + err.Error())
		}
	}
	// Remove from known list
	var newKnown []string
	for _, ws := range cfg.Known {
		if ws != name {
			newKnown = append(newKnown, ws)
		}
	}
	cfg.Known = newKnown
	if err := writeWorkspaceConfig(dataDir, cfg); err != nil {
		output.Warn("failed to update config: " + err.Error())
	}

	fmt.Println()
	output.Success("workspace '" + name + "' deleted")
}

// directoryWorkspace is `cli_workspace resolve`: the workspace a command
// acting on a directory uses, and why (ADR 0072).
type directoryWorkspace struct {
	Workspace string `json:"workspace"`
	Reason    string `json:"reason"`
	Why       string `json:"why"`
	Project   string `json:"project"`
	File      string `json:"file"`
	Proposal  string `json:"proposal"`
	Note      string `json:"note"`
}

// workspacePython runs `python -m imprint.cli_workspace args...` in the
// current directory and returns its standard output. A failure carries the
// message it printed.
func workspacePython(projectDir, dataDir string, args ...string) ([]byte, error) {
	python := platform.VenvPython(projectDir)
	if !platform.FileExists(python) {
		return nil, errors.New("Python venv not found — run 'imprint setup' first")
	}
	child := runner.CommandWithEnv(python, append([]string{"-m", "imprint.cli_workspace"}, args...),
		"PYTHONPATH="+projectDir, "IMPRINT_DATA_DIR="+dataDir)
	var stderr bytes.Buffer
	child.Stderr = &stderr
	out, err := child.Output()
	if err != nil {
		if message := strings.TrimSpace(stderr.String()); message != "" {
			return nil, errors.New(message)
		}
		return nil, err
	}
	return out, nil
}

func resolveWorkspace(projectDir, dataDir, dir string) (directoryWorkspace, error) {
	var ws directoryWorkspace
	out, err := workspacePython(projectDir, dataDir, "resolve", dir)
	if err == nil {
		err = json.Unmarshal(out, &ws)
	}
	return ws, err
}

// pinDirectoryWorkspace pins the workspace of a command that acts on dir:
// IMPRINT_WORKSPACE when set, else dir's own (its approved project file or
// root), else the active one. It says which and why.
func pinDirectoryWorkspace(projectDir, dataDir, dir string) {
	ws, err := resolveWorkspace(projectDir, dataDir, dir)
	if err != nil {
		output.Fail("Could not resolve the workspace for " + dir + ": " + err.Error())
	}
	if ws.Note != "" {
		output.Warn(ws.Note)
	}
	if ws.Proposal != "" && ws.Proposal != ws.Workspace && ws.Reason != "env" {
		output.Warn(ws.File + " asks for workspace " + ws.Proposal + ", which this device has not approved for it. " +
			"Run `imprint workspace use " + ws.Proposal + "` there to approve it.")
	}
	output.Info("Workspace: " + ws.Workspace + " (" + ws.Why + ")")
	if err := os.Setenv("IMPRINT_WORKSPACE", ws.Workspace); err != nil {
		output.Fail(err.Error())
	}
}

const workspaceUseUsage = "usage: imprint workspace use [<name>] [--allow-overlap]\n" +
	"  Without a name, run it in a terminal to pick a workspace."

// parseWorkspaceUse reads `workspace use` arguments. Without a name the
// user picks one, which needs a terminal.
func parseWorkspaceUse(args []string, terminal bool) (name string, allowOverlap bool, err error) {
	for _, arg := range args {
		switch {
		case arg == "--allow-overlap":
			allowOverlap = true
		case strings.HasPrefix(arg, "-") || name != "":
			return "", false, errors.New(workspaceUseUsage)
		default:
			name = arg
		}
	}
	if name == "" && !terminal {
		return "", false, errors.New(workspaceUseUsage)
	}
	if name != "" {
		if err := validateWorkspaceName(name); err != nil {
			return "", false, errors.New("invalid workspace name: " + err.Error())
		}
	}
	return name, allowOverlap, nil
}

// workspaceUse makes this folder's project use a workspace on this device:
// it creates the workspace if new, approves the project folder as one of its
// roots and writes .imprint/workspace.json there. The project folder is the
// one holding a project file, else the git repository's root, else this one.
// The global active workspace is unchanged; git is left to the user.
func workspaceUse(projectDir, dataDir string, args []string) {
	name, allowOverlap, err := parseWorkspaceUse(args, stdinIsTerminal())
	if err != nil {
		output.Fail(err.Error())
	}
	if name == "" {
		cwd, err := os.Getwd()
		if err != nil {
			output.Fail(err.Error())
		}
		here, err := resolveWorkspace(projectDir, dataDir, cwd)
		if err != nil {
			output.Fail("Could not resolve this folder's workspace: " + err.Error())
		}
		if here.Project != "" {
			cwd = here.Project
		}
		name, err = pickWorkspace(readWorkspaceConfig(dataDir).Known, here, cwd, os.Stdin, os.Stdout)
		if err != nil {
			output.Fail(err.Error())
		}
	}
	use := []string{"use", name}
	if allowOverlap {
		use = append(use, "--allow-overlap")
	}
	out, err := workspacePython(projectDir, dataDir, use...)
	if err != nil {
		output.Fail(err.Error())
	}
	var result struct {
		Workspace string   `json:"workspace"`
		Created   bool     `json:"created"`
		Project   string   `json:"project"`
		File      string   `json:"file"`
		RootAdded bool     `json:"root_added"`
		Overlaps  []string `json:"overlaps"`
	}
	if err := json.Unmarshal(out, &result); err != nil {
		output.Fail("Unexpected answer from imprint.cli_workspace: " + err.Error())
	}
	created := ""
	if result.Created {
		created = " (created)"
	}
	output.Success(result.Project + " now uses workspace " + result.Workspace + created + ".")
	if result.RootAdded {
		output.Info("Approved " + result.Project + " as a root of " + result.Workspace + ".")
	}
	if len(result.Overlaps) > 0 {
		output.Info("Shared with " + strings.Join(result.Overlaps, ", ") + ".")
	}
	output.Info("Wrote " + result.File + ". Committing it, or ignoring it in git, is your choice.")
	output.Info("Agent sessions started here use " + result.Workspace + "; `imprint workspace switch` still selects the global one.")
}

// pickWorkspace asks which workspace the project at dir should use: one of
// this device's workspaces, the one its project file asks for, or a new name.
func pickWorkspace(known []string, here directoryWorkspace, dir string, in io.Reader, out io.Writer) (string, error) {
	options := append([]string(nil), known...)
	if here.Proposal != "" && !slices.Contains(options, here.Proposal) {
		options = append(options, here.Proposal)
	}
	fmt.Fprintf(out, "Which workspace should %s use?\n", dir)
	for i, ws := range options {
		var notes []string
		if ws == here.Workspace {
			notes = append(notes, "now: "+here.Why)
		}
		if ws == here.Proposal {
			notes = append(notes, "asked for by "+here.File)
		}
		if !slices.Contains(known, ws) {
			notes = append(notes, "new")
		}
		line := fmt.Sprintf("  %d) %s", i+1, ws)
		if len(notes) > 0 {
			line += " (" + strings.Join(notes, "; ") + ")"
		}
		fmt.Fprintln(out, line)
	}
	fmt.Fprintln(out, "  n) a new workspace")
	reader := bufio.NewReader(in)
	ask := func(prompt string) (string, error) {
		fmt.Fprint(out, prompt)
		line, err := reader.ReadString('\n')
		if err != nil && (err != io.EOF || line == "") {
			return "", errors.New("no workspace chosen")
		}
		return strings.TrimSpace(line), nil
	}
	for {
		choice, err := ask(fmt.Sprintf("Choose 1-%d or n: ", len(options)))
		if err != nil {
			return "", err
		}
		if index, err := strconv.Atoi(choice); err == nil && index >= 1 && index <= len(options) {
			return options[index-1], nil
		}
		if strings.EqualFold(choice, "n") {
			for {
				name, err := ask("New workspace name: ")
				if err != nil {
					return "", err
				}
				if err := validateWorkspaceName(name); err != nil {
					fmt.Fprintln(out, "  "+err.Error())
					continue
				}
				return name, nil
			}
		}
		fmt.Fprintln(out, "  Enter a number from the list, or n.")
	}
}
