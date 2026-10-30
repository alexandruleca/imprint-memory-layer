//go:build linux

package cmd

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func launcherHome(t *testing.T) string {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_DATA_HOME", "")
	t.Setenv("PATH", "") // no update-desktop-database: the files alone count
	return home
}

func TestLinuxLauncherOpensImprintFromTheApplicationMenu(t *testing.T) {
	home := launcherHome(t)
	project := filepath.Join(home, "My Apps", "imprint")
	bin := filepath.Join(project, "bin", "imprint")
	installLauncher(project, bin)

	entry := filepath.Join(home, ".local", "share", "applications", "imprint.desktop")
	icon := filepath.Join(home, ".local", "share", "icons", "hicolor", "scalable", "apps", "imprint.svg")
	content, err := os.ReadFile(entry)
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range []string{"[Desktop Entry]\n", "\nType=Application\n", "\nName=Imprint\n",
		"\nExec=\"" + bin + "\" ui open\n", "\nTryExec=" + bin + "\n", "\nIcon=" + icon + "\n",
		"\nTerminal=false\n", "\nX-Imprint-Project=" + project + "\n"} {
		if !strings.Contains(string(content), line) {
			t.Errorf("entry lacks %q:\n%s", line, content)
		}
	}
	if got, _ := os.ReadFile(icon); !bytes.Equal(got, launcherIcon) {
		t.Error("icon not installed")
	}

	// Setup again changes nothing; uninstall of another installation leaves it.
	installLauncher(project, bin)
	if again, _ := os.ReadFile(entry); !bytes.Equal(again, content) {
		t.Error("a second setup rewrote the entry")
	}
	if err := removeLauncher(filepath.Join(home, "other")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(entry); err != nil {
		t.Fatal("another installation's uninstall removed the entry")
	}
	if err := removeLauncher(project); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{entry, icon} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Errorf("%s left behind", path)
		}
	}
	if err := removeLauncher(project); err != nil {
		t.Fatal("removing an absent launcher:", err)
	}
}

func TestLinuxLauncherFollowsXDGDataHome(t *testing.T) {
	launcherHome(t)
	data := t.TempDir()
	t.Setenv("XDG_DATA_HOME", data)
	installLauncher("/opt/imprint", "/opt/imprint/bin/imprint")
	if _, err := os.Stat(filepath.Join(data, "applications", "imprint.desktop")); err != nil {
		t.Fatal(err)
	}
}

func TestLinuxLauncherLeavesOutAnUnquotablePath(t *testing.T) {
	home := launcherHome(t)
	installLauncher("/opt/$imprint", "/opt/$imprint/bin/imprint")
	if _, err := os.Stat(filepath.Join(home, ".local", "share", "applications", "imprint.desktop")); !os.IsNotExist(err) {
		t.Fatal("wrote an entry for a path it cannot quote")
	}
}
