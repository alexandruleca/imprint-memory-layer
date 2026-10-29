package cmd

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hunter/imprint/internal/release"
)

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

func TestUnsupportedPlatformRefusesIntelMacs(t *testing.T) {
	no := func() bool { return false }
	yes := func() bool { return true }
	for _, p := range [][2]string{{"darwin", "arm64"}, {"linux", "amd64"}, {"linux", "arm64"}, {"windows", "amd64"}} {
		if err := unsupportedPlatform(p[0], p[1], no); err != nil {
			t.Errorf("%s/%s: %v", p[0], p[1], err)
		}
	}
	if err := unsupportedPlatform("darwin", "amd64", no); err == nil || !strings.Contains(err.Error(), "Intel Macs are no longer supported") {
		t.Errorf("darwin/amd64 = %v, want the Intel Mac refusal", err)
	}
	if err := unsupportedPlatform("darwin", "amd64", yes); err == nil || !strings.Contains(err.Error(), "Apple silicon build") {
		t.Errorf("darwin/amd64 under Rosetta = %v, want a pointer to the Apple silicon build", err)
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

func TestParseUpdateFlagsChannel(t *testing.T) {
	for args, want := range map[string]release.Channel{"": "", "--dev": release.Dev, "--stable": release.Stable} {
		f, err := parseUpdateFlags(strings.Fields(args))
		if err != nil || f.Channel != want {
			t.Errorf("%q: channel %q, %v; want %q", args, f.Channel, err, want)
		}
	}
	if _, err := parseUpdateFlags([]string{"--version", "v0.24.0", "--dev"}); err == nil {
		t.Error("--version with --dev should be rejected")
	}
}

func TestUpdateChannelFlagBeatsTheSetting(t *testing.T) {
	setting := func(value string, err error) func() (string, error) {
		return func() (string, error) { return value, err }
	}
	broken := setting("", errors.New("no venv"))
	cases := []struct {
		flag    release.Channel
		setting func() (string, error)
		want    release.Channel
	}{
		{"", setting("stable", nil), release.Stable},
		{"", setting("dev", nil), release.Dev},
		{release.Stable, setting("dev", nil), release.Stable},
		{release.Dev, setting("stable", nil), release.Dev},
		// A flag does not need the setting at all.
		{release.Stable, broken, release.Stable},
	}
	for _, c := range cases {
		if got, err := updateChannel(c.flag, c.setting); err != nil || got != c.want {
			t.Errorf("flag %q: %q, %v; want %q", c.flag, got, err, c.want)
		}
	}
	if _, err := updateChannel("", broken); err == nil || !strings.Contains(err.Error(), "pass --stable or --dev") {
		t.Errorf("an unreadable setting should fail and name the flags: %v", err)
	}
	if _, err := updateChannel("", setting("nightly", nil)); err == nil {
		t.Error("an unknown channel should fail")
	}
}
