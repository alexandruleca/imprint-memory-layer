package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/hunter/imprint/internal/platform"
)

// A native executable stands in for Electron, without a shell or Python venv.
func init() {
	if capture := os.Getenv("IMPRINT_TEST_DESKTOP_ARGS"); capture != "" {
		b, _ := json.Marshal(os.Args[1:])
		if err := os.WriteFile(capture, b, 0o600); err != nil {
			os.Exit(1)
		}
		os.Exit(0)
	}
}

func TestUIOpenLaunchesShellBeforeRuntimeStartup(t *testing.T) {
	dataDir := t.TempDir()
	capture := filepath.Join(t.TempDir(), "desktop args.json")
	t.Setenv("IMPRINT_DATA_DIR", dataDir)
	t.Setenv("IMPRINT_ELECTRON", os.Args[0])
	t.Setenv("IMPRINT_TEST_DESKTOP_ARGS", capture)
	uiOpen([]string{"--port", "18420"})
	deadline := time.Now().Add(5 * time.Second)
	for {
		b, err := os.ReadFile(capture)
		if err == nil {
			var args []string
			if err := json.Unmarshal(b, &args); err != nil {
				t.Fatal(err)
			}
			joined := strings.Join(args, "\n")
			project := platform.FindProjectDir()
			for _, expected := range []string{"--imprint-url=http://127.0.0.1:18420", "--data-dir=" + dataDir,
				"--project-dir=" + project, "--python=" + platform.VenvPython(project)} {
				if !strings.Contains(joined, expected) {
					t.Fatalf("missing argument %q in %v", expected, args)
				}
			}
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("desktop shell was not launched")
		}
		time.Sleep(20 * time.Millisecond)
	}
	if _, ok := readUIState(dataDir); ok {
		t.Fatal("opening the shell must not start an independent daemon")
	}
}
