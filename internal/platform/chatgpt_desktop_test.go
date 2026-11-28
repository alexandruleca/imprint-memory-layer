package platform

import (
	"os"
	"path/filepath"
	"testing"
)

// ChatGPT Desktop is found where its installer puts it, else as the
// Microsoft Store package; with neither, the installer's path is reported,
// and it does not exist.
func TestChatGPTDesktopIsFoundFromTheInstallerOrTheStore(t *testing.T) {
	local := t.TempDir()
	installer := filepath.Join(local, "Programs", "OpenAI", "ChatGPT")
	if got := chatGPTDesktopIn(local); got != installer || DirExists(got) {
		t.Fatalf("nothing installed: %s", got)
	}

	store := filepath.Join(local, "Packages", "OpenAI.ChatGPT-Desktop_2p2nqsd0c76g0")
	if err := os.MkdirAll(filepath.Join(local, "Packages", "OpenAI.Codex_2p2nqsd0c76g0"), 0o700); err != nil {
		t.Fatal(err)
	}
	if got := chatGPTDesktopIn(local); got != installer {
		t.Fatalf("the Codex app counted as ChatGPT Desktop: %s", got)
	}
	if err := os.MkdirAll(store, 0o700); err != nil {
		t.Fatal(err)
	}
	if got := chatGPTDesktopIn(local); got != store {
		t.Fatalf("store app: %s", got)
	}

	if err := os.MkdirAll(installer, 0o700); err != nil {
		t.Fatal(err)
	}
	if got := chatGPTDesktopIn(local); got != installer {
		t.Fatalf("installer app: %s", got)
	}
}
