//go:build !linux

package cmd

// Only the Linux desktop app ships as an AppImage that setup prepares.
func prepareDesktopApp(string) {}

func unpackedDesktopApp(appImage string) string { return "" }
