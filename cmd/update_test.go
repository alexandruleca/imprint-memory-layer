package cmd

import (
	"os"
	"path/filepath"
	"testing"
)

func TestIsNewerVersion(t *testing.T) {
	cases := []struct {
		latest, current string
		want            bool
	}{
		{"v0.14.0", "v0.13.1", true},
		{"v0.13.10", "v0.13.9", true},
		{"v1.0.0", "v0.99.99", true},
		{"v0.13.1", "v0.13.1", false},
		{"v0.13.0", "v0.13.1", false},
		{"v0.14.0", "v0.13.1-4-gabc1234-dirty", true},
		{"v0.13.1", "v0.13.1-4-gabc1234", false},
		{"v0.14.0", "dev", false},
		{"nightly", "v0.13.1", false},
	}
	for _, c := range cases {
		if got := isNewerVersion(c.latest, c.current); got != c.want {
			t.Errorf("isNewerVersion(%q, %q) = %v, want %v", c.latest, c.current, got, c.want)
		}
	}
}

func TestInstallKindOf(t *testing.T) {
	inno := t.TempDir()
	if err := os.WriteFile(filepath.Join(inno, "unins000.exe"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	script := t.TempDir()
	cases := []struct {
		dir, goos string
		want      installKind
	}{
		{inno, "windows", installWindowsSetup},
		{script, "windows", installArchive},
		{"/Applications/Imprint.app/Contents/Resources/imprint", "darwin", installMacPkg},
		{"/Users/me/.local/share/imprint", "darwin", installArchive},
		{"/home/me/.local/share/imprint", "linux", installArchive},
	}
	for _, c := range cases {
		if got := installKindOf(c.dir, c.goos); got != c.want {
			t.Errorf("installKindOf(%q, %q) = %q, want %q", c.dir, c.goos, got, c.want)
		}
	}
}

func TestMoveBinariesAsideKeepsOneBackupOfEachFile(t *testing.T) {
	bin := t.TempDir()
	write := func(name, body string) {
		if err := os.WriteFile(filepath.Join(bin, name), []byte(body), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	write("imprint", "new-old")
	write("imprint.prev", "older")
	write("imprint-ui", "ui")
	if err := os.Mkdir(filepath.Join(bin, "imprint-ui.app"), 0o755); err != nil {
		t.Fatal(err)
	}

	moveBinariesAside(bin)

	for name, want := range map[string]string{"imprint.prev": "new-old", "imprint-ui.prev": "ui"} {
		got, err := os.ReadFile(filepath.Join(bin, name))
		if err != nil || string(got) != want {
			t.Errorf("%s = %q (%v), want %q", name, got, err, want)
		}
	}
	for _, gone := range []string{"imprint", "imprint-ui", "imprint.prev.prev"} {
		if _, err := os.Stat(filepath.Join(bin, gone)); !os.IsNotExist(err) {
			t.Errorf("%s should not exist after moving binaries aside", gone)
		}
	}
	if info, err := os.Stat(filepath.Join(bin, "imprint-ui.app")); err != nil || !info.IsDir() {
		t.Errorf("app bundle directories stay in place")
	}
}

func TestParseUpdateFlagsJSONNeedsCheck(t *testing.T) {
	if f, err := parseUpdateFlags([]string{"--check", "--json"}); err != nil || !f.Check || !f.JSON {
		t.Fatalf("--check --json = %+v, %v", f, err)
	}
	if _, err := parseUpdateFlags([]string{"--json"}); err == nil {
		t.Fatal("--json without --check should be rejected")
	}
}
