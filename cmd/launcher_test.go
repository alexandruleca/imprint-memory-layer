package cmd

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestLauncherIconIsTheSiteLogo(t *testing.T) {
	logo, err := os.ReadFile(filepath.Join("..", "site", "public", "logo.svg"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(logo, launcherIcon) {
		t.Fatal("cmd/launcher_icon.svg differs from site/public/logo.svg; copy the logo again")
	}
}

func TestLauncherRefusesPathsItCannotQuote(t *testing.T) {
	for _, path := range []string{`/home/a"b/imprint`, "/home/$USER/imprint", `C:\imprint`, "/home/it's/imprint", "/home/a\nb"} {
		if !launcherPathUnsafe("/home/me/.local/share/imprint", path) {
			t.Errorf("%q should be refused", path)
		}
	}
	if launcherPathUnsafe("/home/me/My Apps/imprint", "/home/me/My Apps/imprint/bin/imprint") {
		t.Error("spaces are quoted, not refused")
	}
}
