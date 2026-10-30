//go:build linux

package cmd

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
)

// launcherPaths are the desktop entry and its icon, in the user's XDG data
// directory, where GNOME, KDE and other desktops look for applications.
func launcherPaths() (entry, icon string) {
	base := os.Getenv("XDG_DATA_HOME")
	if !filepath.IsAbs(base) {
		base = filepath.Join(platform.HomeDir(), ".local", "share")
	}
	return filepath.Join(base, "applications", "imprint.desktop"),
		filepath.Join(base, "icons", "hicolor", "scalable", "apps", "imprint.svg")
}

// desktopEntry is the application entry. X-Imprint-Project names the
// installation it belongs to, so uninstall removes only its own.
func desktopEntry(project, bin, icon string) string {
	return "[Desktop Entry]\n" +
		"Type=Application\n" +
		"Version=1.5\n" +
		"Name=Imprint\n" +
		"GenericName=AI memory\n" +
		"Comment=Open the Imprint dashboard, starting Imprint if it is not running\n" +
		"Exec=\"" + strings.ReplaceAll(bin, "%", "%%") + "\" ui open\n" +
		"TryExec=" + bin + "\n" +
		"Icon=" + icon + "\n" +
		"Terminal=false\n" +
		"Categories=Utility;\n" +
		"Keywords=memory;mcp;agent;ai;dashboard;echo;\n" +
		"StartupNotify=false\n" +
		"X-Imprint-Project=" + project + "\n"
}

// installLauncher writes the desktop entry and icon when they differ.
func installLauncher(project, bin string) {
	if launcherPathUnsafe(project, bin) {
		output.Warn("No application menu entry: the install path has characters a desktop entry cannot quote. Open Imprint with: imprint ui open")
		return
	}
	entry, icon := launcherPaths()
	content := desktopEntry(project, bin, icon)
	current, _ := os.ReadFile(entry)
	currentIcon, _ := os.ReadFile(icon)
	if string(current) == content && bytes.Equal(currentIcon, launcherIcon) {
		output.Skip("Imprint in the application menu")
		return
	}
	// The icon first: an entry is never left pointing at a missing icon.
	err := writeLauncherFile(icon, launcherIcon)
	if err == nil {
		err = writeLauncherFile(entry, []byte(content))
	}
	if err != nil {
		output.Warn("Could not add Imprint to the application menu: " + err.Error())
		return
	}
	refreshDesktopMenu(filepath.Dir(entry))
	output.Success("Imprint in the application menu (" + entry + ")")
}

func writeLauncherFile(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	return os.WriteFile(path, data, 0644)
}

// removeLauncher removes the desktop entry and icon if they belong to project.
func removeLauncher(project string) error {
	entry, icon := launcherPaths()
	content, err := os.ReadFile(entry)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !strings.Contains(string(content), "\nX-Imprint-Project="+project+"\n") {
		return nil
	}
	if err := os.Remove(entry); err != nil {
		return err
	}
	if err := os.Remove(icon); err != nil && !os.IsNotExist(err) {
		return err
	}
	refreshDesktopMenu(filepath.Dir(entry))
	return nil
}

// refreshDesktopMenu updates the menu cache where the desktop keeps one; most
// desktops also notice the change on their own.
func refreshDesktopMenu(dir string) {
	if tool, err := exec.LookPath("update-desktop-database"); err == nil {
		_ = exec.Command(tool, dir).Run()
	}
}
