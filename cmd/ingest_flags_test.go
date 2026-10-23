package cmd

import (
	"os"
	"os/exec"
	"reflect"
	"strings"
	"testing"
)

func TestIndexFlagsPreservePathAndOriginalQueueArgs(t *testing.T) {
	original := []string{"--force", "/path with spaces/repo", "--batch-size=1"}
	batch, remaining := parseBatchSizeFlag(original)
	force, remaining := parseForceFlag(remaining)
	if batch != 1 || !force || !reflect.DeepEqual(remaining, []string{"/path with spaces/repo"}) {
		t.Fatalf("wrong parsed flags: %d %t %v", batch, force, remaining)
	}
	if !reflect.DeepEqual(original, []string{"--force", "/path with spaces/repo", "--batch-size=1"}) {
		t.Fatal("parsing modified queued arguments")
	}
}

func TestIndexFileReportsPythonFailure(t *testing.T) {
	if os.Getenv("IMPRINT_TEST_INDEX_FAILURE") == "1" {
		// The test executable rejects Python's -c/-m flags. Detection falls
		// back to the directory name; indexing must then fail the CLI.
		indexFile(os.Args[0], nil, "fixture.py", t.TempDir(), 1, true)
		return
	}
	child := exec.Command(os.Args[0], "-test.run=^TestIndexFileReportsPythonFailure$")
	child.Env = append(os.Environ(), "IMPRINT_TEST_INDEX_FAILURE=1")
	output, err := child.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "Indexing command failed:") {
		t.Fatalf("index failure was not propagated: err=%v output=%s", err, output)
	}
}
