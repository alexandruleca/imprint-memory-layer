package cmd

import (
	_ "embed"
	"strings"
)

// Setup adds Imprint to the system's app launcher, so it can be searched for
// and started like any app. The entry runs `imprint ui open`, which starts the
// dashboard or shows the one already running. The Windows installer and the
// macOS package bring their own (a Start Menu entry, /Applications/Imprint.app);
// setup adds one on Linux and for a CLI install on macOS.

// launcherIcon is site/public/logo.svg (launcher_test.go keeps them equal).
//
//go:embed launcher_icon.svg
var launcherIcon []byte

// launcherPathUnsafe reports a path the launcher could not quote reliably for
// every desktop's parser; setup then leaves the launcher out and says so.
func launcherPathUnsafe(paths ...string) bool {
	for _, p := range paths {
		if strings.ContainsAny(p, "\"`$\\'\n\r") {
			return true
		}
	}
	return false
}
