package cmd

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
)

func TestLearnStoresCoverLocalProfilesAndRegisteredWSLProfiles(t *testing.T) {
	project := t.TempDir()
	claude, codex := filepath.Join(project, "claude profile"), filepath.Join(project, "codex profile")
	t.Setenv("CLAUDE_CONFIG_DIR", claude)
	t.Setenv("CODEX_HOME", codex)
	t.Setenv("HOME", project)
	t.Setenv("USERPROFILE", project)
	// Cline's sessions follow its data directory override.
	t.Setenv("CLINE_DIR", "")
	t.Setenv("CLINE_DATA_DIR", filepath.Join(project, "cline data"))
	t.Setenv("CLINE_SESSION_DATA_DIR", "")
	t.Setenv("COPILOT_HOME", filepath.Join(project, "copilot home"))
	profile := wslProfile{Distribution: "Ubuntu", Home: "/home/test", ClaudeHome: "/home/test/.config/claude",
		ClaudeRegistry: "/home/test/.config/claude/.claude.json", CodexHome: "/home/test/codex", CursorHome: "/home/test/.cursor",
		ClineHome: "/home/test/.cline", CopilotHome: "/home/test/.copilot"}
	records := []wslRegistration{
		{Profile: profile, Target: "claude-code"},
		{Profile: profile, Target: "codex"},
		{Profile: profile, Target: "cursor"},
		{Profile: profile, Target: "cline"},
		{Profile: profile, Target: "copilot"},
		{Profile: profile, Target: "codex"}, // the same store reachable twice
		{Profile: wslProfile{Distribution: "Debian", Home: "/home/other", ClaudeHome: "/home/other/.claude"}, User: "other", Target: "claude-code"},
		{Profile: wslProfile{Distribution: "Stopped", Home: "/home/test", CodexHome: "/home/test/.codex"}, Target: "codex"},
		{Profile: wslProfile{Distribution: "Ubuntu", Home: "/home/test"}, Target: "codex"}, // no Codex home recorded
	}
	encoded, _ := json.Marshal(records)
	if err := os.WriteFile(wslRegistrationFile(project), encoded, 0600); err != nil {
		t.Fatal(err)
	}
	old := runWSL
	t.Cleanup(func() { runWSL = old })
	var calls [][]string
	runWSL = func(args ...string) (string, error) {
		calls = append(calls, args)
		if args[len(args)-1] != wslDriveMounts || args[len(args)-2] != "-c" {
			t.Fatalf("learn must only read the mount table: %v", args)
		}
		if args[1] == "Stopped" {
			return "", errors.New("the distribution is not available")
		}
		return "C:\\134\t/mnt/c\n", nil
	}

	groups, problems := learnStores(project)
	want := []conversationStores{
		{label: "this computer", stores: [][2]string{{"claude-code", claude}, {"cursor", filepath.Join(project, ".cursor")}, {"codex", codex},
			{"copilot", filepath.Join(project, "copilot home")}, {"cline", filepath.Join(project, "cline data", "sessions")}}},
		{label: "WSL Ubuntu", distribution: "Ubuntu", home: "/home/test", mounts: "C:\\134\t/mnt/c\n",
			stores: [][2]string{{"claude-code", "/home/test/.config/claude"}, {"codex", "/home/test/codex"}, {"cursor", "/home/test/.cursor"},
				{"cline", "/home/test/.cline/data/sessions"}, {"copilot", "/home/test/.copilot"}}},
		{label: "WSL Debian (other)", distribution: "Debian", user: "other", home: "/home/other", mounts: "C:\\134\t/mnt/c\n",
			stores: [][2]string{{"claude-code", "/home/other/.claude"}}},
	}
	if !reflect.DeepEqual(groups, want) {
		t.Fatalf("groups = %+v\nwant %+v", groups, want)
	}
	if len(problems) != 2 || !strings.Contains(problems[0], "Ubuntu codex: invalid") ||
		!strings.Contains(problems[1], "WSL Stopped is unavailable") || !strings.Contains(problems[1], "not available") {
		t.Fatalf("unreachable stores must be reported: %q", problems)
	}
	// One mount-table read per distribution and user, as that user.
	if len(calls) != 3 || !slices.Contains(calls[1], "--user") || calls[1][3] != "other" {
		t.Fatalf("WSL calls = %q", calls)
	}
}

func TestLearnStoresWithoutRegistrationsAreLocalOnly(t *testing.T) {
	old := runWSL
	t.Cleanup(func() { runWSL = old })
	runWSL = func(args ...string) (string, error) {
		t.Fatalf("no WSL call without a registration: %v", args)
		return "", nil
	}
	groups, problems := learnStores(t.TempDir())
	if len(groups) != 1 || groups[0].distribution != "" || len(problems) != 0 {
		t.Fatalf("groups = %+v, problems = %q", groups, problems)
	}
}

func TestLearnRunsEachStoreGroupInItsOwnPathContext(t *testing.T) {
	wsl := conversationStores{label: "WSL Ubuntu", distribution: "Ubuntu", home: "/home/test", mounts: "C:\\134\t/mnt/c\n",
		stores: [][2]string{{"claude-code", "/home/test/.claude"}, {"codex", "/home/test/.codex"}}}
	if got := wsl.learnArgs(); !reflect.DeepEqual(got, []string{"-m", "imprint.cli_conversations",
		"--store", "claude-code", "/home/test/.claude", "--store", "codex", "/home/test/.codex"}) {
		t.Fatalf("args = %q", got)
	}
	if got := wsl.pathContext(); !reflect.DeepEqual(got, []string{"IMPRINT_WSL_DISTRO=Ubuntu", "IMPRINT_WSL_MOUNTS=C:\\134\t/mnt/c\n", "IMPRINT_WSL_HOME=/home/test"}) {
		t.Fatalf("WSL context = %q", got)
	}
	// This computer's stores clear a context inherited from a bridged caller.
	local := conversationStores{label: "this computer"}
	if got := local.pathContext(); !reflect.DeepEqual(got, []string{"IMPRINT_WSL_DISTRO=", "IMPRINT_WSL_MOUNTS=", "IMPRINT_WSL_HOME="}) {
		t.Fatalf("local context = %q", got)
	}
	if !strings.Contains(wslLauncher(wslProfile{Distribution: "Ubuntu", Home: "/home/test"}, backendPaths{}, "imprint-wsl"), "mounts=$("+wslDriveMounts+")\n") {
		t.Fatal("the launcher and learn must read the same mount table")
	}
}
