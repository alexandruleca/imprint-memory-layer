//go:build darwin

package cmd

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMacLauncherAppOpensImprintForACLIInstall(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	project := filepath.Join(home, ".local", "share", "imprint")
	bin := filepath.Join(project, "bin", "imprint")
	installLauncher(project, bin)

	app := filepath.Join(home, "Applications", "Imprint.app")
	script, err := os.ReadFile(filepath.Join(app, "Contents", "MacOS", "imprint-open"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(script), "exec '"+bin+"' ui open") {
		t.Errorf("script does not open Imprint:\n%s", script)
	}
	if info, _ := os.Stat(filepath.Join(app, "Contents", "MacOS", "imprint-open")); info.Mode()&0111 == 0 {
		t.Error("script is not executable")
	}
	plist, err := os.ReadFile(filepath.Join(app, "Contents", "Info.plist"))
	if err != nil || !strings.Contains(string(plist), "<string>imprint-open</string>") {
		t.Fatalf("plist: %v\n%s", err, plist)
	}

	if err := removeLauncher(filepath.Join(home, "other")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(app); err != nil {
		t.Fatal("another installation's uninstall removed the app")
	}
	if err := removeLauncher(project); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(app); !os.IsNotExist(err) {
		t.Fatal("app left behind")
	}
}

func TestMacLauncherSkipsThePackageAndForeignApps(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	app := filepath.Join(home, "Applications", "Imprint.app")

	installLauncher("/Applications/Imprint.app/Contents/Resources/imprint", "/Applications/Imprint.app/Contents/Resources/imprint/bin/imprint")
	if _, err := os.Stat(app); !os.IsNotExist(err) {
		t.Fatal("a package install got a second app")
	}

	if err := os.MkdirAll(app, 0755); err != nil {
		t.Fatal(err)
	}
	installLauncher(filepath.Join(home, "imprint"), filepath.Join(home, "imprint", "bin", "imprint"))
	if _, err := os.Stat(filepath.Join(app, "Contents", "MacOS", "imprint-open")); !os.IsNotExist(err) {
		t.Fatal("overwrote an Imprint.app setup did not make")
	}
}
