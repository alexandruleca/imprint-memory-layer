package cmd

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// Setup installs the optional tools that are turned on with its own uv
// (ADR 0121); a tool that cannot be installed does not fail setup.
func TestSetupInstallsTheEnabledOptionalToolsWithItsUv(t *testing.T) {
	project, dataDir := t.TempDir(), filepath.Join(t.TempDir(), "data")
	for _, answer := range []string{"kreuzberg: enabled (installed)\n", "obscura: enabled BUT offline\n", ""} {
		log := filepath.Join(t.TempDir(), "calls")
		t.Setenv("IMPRINT_FAKE_TOOL_LOG", log)
		t.Setenv("IMPRINT_FAKE_TOOLS", answer)
		installOptionalTools("/opt/imprint/bin/uv", os.Args[0], project, dataDir)
		if got, want := fakeCalls(t, log), []string{"optional-tools uv=/opt/imprint/bin/uv data=data"}; !reflect.DeepEqual(got, want) {
			t.Errorf("calls %v, want %v", got, want)
		}
	}
}
