//go:build darwin

package cmd

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
)

const launcherMarker = "# imprint-project: "

// launcherApp is the app a CLI install gets, where Spotlight and Launchpad
// find it. The macOS package installs /Applications/Imprint.app instead.
func launcherApp() string {
	return filepath.Join(platform.HomeDir(), "Applications", "Imprint.app")
}

// launcherScript is the app's executable. The marker names the installation
// it belongs to, so uninstall removes only its own.
func launcherScript(project, bin string) string {
	return "#!/bin/sh\n" +
		"# Written by imprint setup: open the Imprint dashboard, starting Imprint if it is not running.\n" +
		launcherMarker + project + "\n" +
		"exec '" + bin + "' ui open >/dev/null 2>&1\n"
}

// launcherPlist describes a background app: running the launcher shows no
// Dock icon of its own; the desktop app it opens decides that.
func launcherPlist() string {
	return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Imprint</string>
    <key>CFBundleDisplayName</key>
    <string>Imprint</string>
    <key>CFBundleIdentifier</key>
    <string>com.alexandruleca.imprint.launcher</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>imprint-open</string>
    <key>CFBundleIconFile</key>
    <string>imprint</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.developer-tools</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
`
}

// installLauncher writes ~/Applications/Imprint.app for a CLI install.
func installLauncher(project, bin string) {
	if strings.Contains(project, ".app/Contents/") {
		return // the package's /Applications/Imprint.app already opens Imprint
	}
	if launcherPathUnsafe(project, bin) {
		output.Warn("No Imprint app in ~/Applications: the install path has characters a launcher cannot quote. Open Imprint with: imprint ui open")
		return
	}
	app := launcherApp()
	executable := filepath.Join(app, "Contents", "MacOS", "imprint-open")
	script := launcherScript(project, bin)
	current, err := os.ReadFile(executable)
	if err == nil && string(current) == script {
		output.Skip("Imprint app in ~/Applications")
		return
	}
	if (err == nil && !strings.Contains(string(current), "\n"+launcherMarker)) || (os.IsNotExist(err) && platform.DirExists(app)) {
		output.Warn("Left " + app + " alone: it was not made by imprint setup. Open Imprint with: imprint ui open")
		return
	}
	err = os.MkdirAll(filepath.Join(app, "Contents", "Resources"), 0755)
	if err == nil {
		err = os.MkdirAll(filepath.Dir(executable), 0755)
	}
	if err == nil {
		err = os.WriteFile(filepath.Join(app, "Contents", "Info.plist"), []byte(launcherPlist()), 0644)
	}
	if err == nil {
		err = os.WriteFile(executable, []byte(script), 0755)
	}
	if err != nil {
		output.Warn("Could not add the Imprint app to ~/Applications: " + err.Error())
		return
	}
	renderLauncherIcon(filepath.Join(app, "Contents", "Resources", "imprint.icns"))
	// Spotlight finds the app on its own; registering makes it searchable at once.
	_ = exec.Command("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister", "-f", app).Run()
	output.Success("Imprint app in ~/Applications: find it in Spotlight or Launchpad")
}

// renderLauncherIcon turns the logo into the app icon with tools macOS ships;
// without them the app keeps the generic icon.
func renderLauncherIcon(icns string) {
	work, err := os.MkdirTemp("", "imprint-icon-")
	if err != nil {
		return
	}
	defer os.RemoveAll(work)
	svg := filepath.Join(work, "imprint.svg")
	if os.WriteFile(svg, launcherIcon, 0644) != nil {
		return
	}
	if exec.Command("qlmanage", "-t", "-s", "512", "-o", work, svg).Run() != nil {
		return
	}
	_ = exec.Command("sips", "-s", "format", "icns", svg+".png", "--out", icns).Run()
}

// removeLauncher removes ~/Applications/Imprint.app if it belongs to project.
func removeLauncher(project string) error {
	app := launcherApp()
	content, err := os.ReadFile(filepath.Join(app, "Contents", "MacOS", "imprint-open"))
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !strings.Contains(string(content), "\n"+launcherMarker+project+"\n") {
		return nil
	}
	return os.RemoveAll(app)
}
