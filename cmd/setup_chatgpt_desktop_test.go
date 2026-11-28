package cmd

import (
	"os"
	"path/filepath"
	"testing"
)

// chatGPTDesktopCase: a home whose Codex profile holds this runtime's entry,
// and ChatGPT Desktop's install marker at a path the test creates or not.
func chatGPTDesktopCase(t *testing.T, wsl bool) (project, marker string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("CODEX_HOME", filepath.Join(home, ".codex"))
	project = filepath.Join(home, "runtime")
	writeCleanupFixture(t, filepath.Join(home, ".codex", "config.toml"),
		"[mcp_servers.imprint]\ncommand = \""+filepath.ToSlash(filepath.Join(project, "python"))+"\"\nargs = [\"-m\", \"imprint\"]\n")
	marker = filepath.Join(home, "AppData", "Local", "Programs", "OpenAI", "ChatGPT")
	previousWSL, previousMarker := onWSL, chatGPTDesktopMarker
	t.Cleanup(func() { onWSL, chatGPTDesktopMarker = previousWSL, previousMarker })
	onWSL = func() bool { return wsl }
	chatGPTDesktopMarker = func() string { return marker }
	return project, marker
}

func connectedClient(t *testing.T, name, project string) bool {
	t.Helper()
	selected, found := selectClients(name)
	if !found {
		t.Fatalf("no client %s", name)
	}
	ok, err := isConnected(selected[0], project)
	if err != nil {
		t.Fatal(err)
	}
	return ok
}

// ChatGPT Desktop shares Codex's profile, so Codex's entry is its entry, but
// only while ChatGPT Desktop is installed: without it, Codex alone is
// connected, and disconnecting ChatGPT Desktop leaves Codex alone.
func TestChatGPTDesktopIsConnectedOnlyWhenInstalled(t *testing.T) {
	project, marker := chatGPTDesktopCase(t, false)

	if !connectedClient(t, "codex", project) || connectedClient(t, "chatgpt-desktop", project) {
		t.Fatal("without ChatGPT Desktop installed, Codex's entry showed it connected")
	}
	selected, _ := selectClients("chatgpt-desktop")
	if err := disconnectClients(selected, project); err != nil {
		t.Fatal(err)
	}
	if !connectedClient(t, "codex", project) {
		t.Fatal("disconnecting an uninstalled ChatGPT Desktop disconnected Codex")
	}

	if err := os.MkdirAll(marker, 0o700); err != nil {
		t.Fatal(err)
	}
	if !connectedClient(t, "chatgpt-desktop", project) {
		t.Fatal("installed ChatGPT Desktop with the shared entry is not connected")
	}
	if err := disconnectClients(selected, project); err != nil {
		t.Fatal(err)
	}
	if connectedClient(t, "codex", project) || connectedClient(t, "chatgpt-desktop", project) {
		t.Fatal("disconnecting an installed ChatGPT Desktop left the shared entry")
	}
}

// From WSL, ChatGPT Desktop (a Windows app) never counts the Linux Codex
// profile as its own, installed or not.
func TestChatGPTDesktopFromWSLIsNeverConnected(t *testing.T) {
	project, marker := chatGPTDesktopCase(t, true)
	if err := os.MkdirAll(marker, 0o700); err != nil {
		t.Fatal(err)
	}
	if !connectedClient(t, "codex", project) || connectedClient(t, "chatgpt-desktop", project) {
		t.Fatal("from WSL, the Linux Codex entry showed ChatGPT Desktop connected")
	}
}
