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

// The desktop app reads this when the OS starts it without `ui open`'s
// arguments; it must name the same installation those arguments do.
func TestUIRuntimePrintsTheInstallationUIOpenPasses(t *testing.T) {
	dataDir := t.TempDir()
	t.Setenv("IMPRINT_DATA_DIR", dataDir)
	read, write, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout := os.Stdout
	os.Stdout = write
	UI([]string{"runtime"})
	os.Stdout = stdout
	write.Close()
	var printed map[string]string
	if err := json.NewDecoder(read).Decode(&printed); err != nil {
		t.Fatal(err)
	}
	project := platform.FindProjectDir()
	want := map[string]string{"project_dir": project, "data_dir": dataDir, "python": platform.VenvPython(project)}
	if len(printed) != len(want) {
		t.Fatalf("printed %v, want %v", printed, want)
	}
	for key, value := range want {
		if printed[key] != value {
			t.Fatalf("%s = %q, want %q", key, printed[key], value)
		}
	}
}
