package cmd

import (
	"path/filepath"
	"reflect"
	"testing"
)

func TestRestoreArgsMakeTheFolderAndDestinationAbsolute(t *testing.T) {
	t.Chdir(t.TempDir())
	here, err := filepath.Abs(".")
	if err != nil {
		t.Fatal(err)
	}
	cases := map[string]struct{ in, want []string }{
		"folder": {[]string{"plans", "--apply"}, []string{filepath.Join(here, "plans"), "--apply"}},
		"folder elsewhere": {[]string{"--all", "plans", "--to", "out", "--overwrite"},
			[]string{"--all", filepath.Join(here, "plans"), "--to", filepath.Join(here, "out"), "--overwrite"}},
		"project": {[]string{"--project", "notes", "--to=out", "--workspace", "work"},
			[]string{"--project", "notes", "--to=" + filepath.Join(here, "out"), "--workspace", "work"}},
	}
	for name, c := range cases {
		if got := restoreArgs(c.in); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: restoreArgs(%q) = %q, want %q", name, c.in, got, c.want)
		}
	}
}
