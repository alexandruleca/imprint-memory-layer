//go:build linux

package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/runner"
)

// The Linux desktop app ships as an AppImage. Run as is, it needs libfuse2,
// which Ubuntu 24.04 no longer installs, and Electron's sandbox needs user
// namespaces, which Ubuntu 24.04's AppArmor grants only to programs with a
// profile. Setup unpacks the AppImage beside itself and, where AppArmor
// restricts user namespaces, installs such a profile as the VS Code and Slack
// packages do. Without it the app exits at startup and `ui open` falls back
// to the browser.

const userNamespacesRestricted = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns"

// unpackedDesktopApp is the unpacked AppImage's executable.
func unpackedDesktopApp(appImage string) string {
	return filepath.Join(appImage+".d", "imprint-ui")
}

// prepareDesktopApp unpacks bin/imprint-ui when it changed since the last
// unpack and lets the unpacked app create user namespaces.
func prepareDesktopApp(projectDir string) {
	appImage := filepath.Join(projectDir, "bin", "imprint-ui")
	info, err := os.Stat(appImage)
	if err != nil {
		return
	}
	dir := appImage + ".d"
	stamp := fmt.Sprintf("%d %d", info.Size(), info.ModTime().UnixNano())
	if b, err := os.ReadFile(filepath.Join(dir, ".appimage")); err != nil || string(b) != stamp {
		output.Info("Unpacking the desktop app (no FUSE needed)...")
		if err := unpackAppImage(appImage, dir, stamp); err != nil {
			output.Warn("Could not unpack the desktop app: " + err.Error())
			return
		}
	}
	allowUserNamespaces(unpackedDesktopApp(appImage))
}

// unpackAppImage extracts appImage next to dir and swaps it in. The AppImage
// runtime extracts without FUSE, into squashfs-root under its working directory.
func unpackAppImage(appImage, dir, stamp string) error {
	work, err := os.MkdirTemp(filepath.Dir(dir), ".imprint-ui-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(work)
	extract := exec.Command(appImage, "--appimage-extract")
	extract.Dir = work
	if out, err := extract.CombinedOutput(); err != nil {
		return fmt.Errorf("%v: %s", err, lastNonBlankLines(string(out), 2))
	}
	root := filepath.Join(work, "squashfs-root")
	if err := os.WriteFile(filepath.Join(root, ".appimage"), []byte(stamp), 0644); err != nil {
		return err
	}
	if err := os.RemoveAll(dir); err != nil {
		return err
	}
	return os.Rename(root, dir)
}

// appArmorProfile grants bin user namespaces and nothing else changes: like
// the profiles Ubuntu ships for VS Code and Slack, it is unconfined.
func appArmorProfile(name, bin string) string {
	return fmt.Sprintf(`# Written by imprint setup: lets the Imprint desktop app sandbox itself.
abi <abi/4.0>,
include <tunables/global>

profile %s %q flags=(unconfined) {
  userns,

  include if exists <local/%s>
}
`, name, bin, name)
}

// allowUserNamespaces installs an AppArmor profile for bin with sudo, after
// asking, when AppArmor restricts user namespaces and the profile is missing
// or names another path. The profile is per user: installs live in homes.
func allowUserNamespaces(bin string) {
	if b, err := os.ReadFile(userNamespacesRestricted); err != nil || strings.TrimSpace(string(b)) != "1" {
		return
	}
	name := fmt.Sprintf("imprint-ui-%d", os.Getuid())
	path := "/etc/apparmor.d/" + name
	profile := appArmorProfile(name, bin)
	if b, err := os.ReadFile(path); err == nil && string(b) == profile {
		return
	}
	staged := filepath.Join(filepath.Dir(bin), "apparmor-profile")
	if err := os.WriteFile(staged, []byte(profile), 0644); err != nil {
		output.Warn("Could not write the AppArmor profile: " + err.Error())
		return
	}
	manual := "sudo install -m 0644 " + staged + " " + path + " && sudo apparmor_parser -r " + path
	tty, err := os.Open("/dev/tty")
	if nonInteractiveInstall || err != nil {
		output.Warn("AppArmor keeps the desktop app from starting, so `imprint ui open` uses the browser. Allow it with:\n  " + manual)
		return
	}
	tty.Close()
	output.Info("Ubuntu's AppArmor lets the desktop app sandbox itself only with a profile, as it does for VS Code and Slack.")
	if !confirm("Install " + path + " with sudo?") {
		output.Skip("AppArmor profile: `imprint ui open` uses the browser. Allow the app later with:\n  " + manual)
		return
	}
	err = runner.Run("sudo", "install", "-m", "0644", staged, path)
	if err == nil {
		err = runner.Run("sudo", "apparmor_parser", "-r", path)
	}
	if err != nil {
		output.Warn("Installing the AppArmor profile failed: " + err.Error() + "\n  Run: " + manual)
		return
	}
	output.Success("AppArmor profile installed: the desktop app starts sandboxed")
}
