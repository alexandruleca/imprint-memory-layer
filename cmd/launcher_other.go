//go:build !linux && !darwin

package cmd

// The Windows installer adds Imprint to the Start Menu itself.
func installLauncher(project, bin string) {}

func removeLauncher(project string) error { return nil }
