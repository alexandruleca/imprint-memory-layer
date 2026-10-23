package release

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVerifyChecksumBeforeInstallation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "archive.tar.gz")
	content := []byte("a complete release archive")
	if err := os.WriteFile(path, content, 0600); err != nil {
		t.Fatal(err)
	}
	sum := fmt.Sprintf("%x", sha256.Sum256(content))
	for _, manifest := range []string{sum + "  archive.tar.gz\n", sum + " *archive.tar.gz\r\n"} {
		if err := VerifyChecksum(path, "archive.tar.gz", strings.NewReader(manifest)); err != nil {
			t.Fatal(err)
		}
	}
	for _, manifest := range []string{"", sum + "  other.tar.gz", "invalid archive.tar.gz", sum + "  archive.tar.gz\n" + sum + "  archive.tar.gz"} {
		if err := VerifyChecksum(path, "archive.tar.gz", strings.NewReader(manifest)); err == nil {
			t.Fatalf("accepted invalid manifest %q", manifest)
		}
	}
	if err := os.WriteFile(path, []byte("truncated"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := VerifyChecksum(path, "archive.tar.gz", strings.NewReader(sum+"  archive.tar.gz")); err == nil {
		t.Fatal("accepted corrupted archive")
	}
}
