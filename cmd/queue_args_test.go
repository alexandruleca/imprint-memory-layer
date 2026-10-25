package cmd

import (
	"path/filepath"
	"reflect"
	"testing"
)

func TestQueueArgsMakesPathsAbsoluteForTheDashboard(t *testing.T) {
	t.Chdir(t.TempDir())
	here, err := filepath.Abs(".")
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		args        []string
		firstIsPath bool
		pathFlags   []string
		valueFlags  []string
		want        []string
	}{
		{[]string{"--batch-size", "8", "repo", "--force"}, true, nil, []string{"--batch-size"},
			[]string{"--batch-size", "8", filepath.Join(here, "repo"), "--force"}},
		{[]string{"--batch-size=8", "./repo/"}, true, nil, []string{"--batch-size"},
			[]string{"--batch-size=8", filepath.Join(here, "repo")}},
		{[]string{"--desktop", "--path", "exports", "--interval", "30"}, false, []string{"--path"}, []string{"--interval"},
			[]string{"--desktop", "--path", filepath.Join(here, "exports"), "--interval", "30"}},
		{[]string{"https://example.com", "--from-file=urls.txt", "--project", "docs"}, false, []string{"--from-file"},
			[]string{"--project", "--follow-depth"},
			[]string{"https://example.com", "--from-file=" + filepath.Join(here, "urls.txt"), "--project", "docs"}},
	}
	for _, c := range cases {
		original := append([]string(nil), c.args...)
		if got := queueArgs(c.args, c.firstIsPath, c.pathFlags, c.valueFlags); !reflect.DeepEqual(got, c.want) {
			t.Errorf("queueArgs(%q) = %q, want %q", c.args, got, c.want)
		}
		if !reflect.DeepEqual(c.args, original) {
			t.Errorf("queueArgs modified its input %q", original)
		}
	}
}
