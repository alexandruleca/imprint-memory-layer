package cmd

import (
	"reflect"
	"testing"
)

func TestSyncArgsKeepsSubcommandsAndJoinsCodes(t *testing.T) {
	cases := map[string]struct{ in, want []string }{
		"no arguments":    {nil, []string{"--help"}},
		"forget":          {[]string{"forget", "abc"}, []string{"forget", "abc"}},
		"remove":          {[]string{"remove", "abc"}, []string{"remove", "abc"}},
		"share code":      {[]string{"7f3a9c21-Ab3xY9Kq", "--two-way"}, []string{"join", "7f3a9c21-Ab3xY9Kq", "--two-way"}},
		"code with relay": {[]string{"sync.example.com/7f3a9c21-Ab3xY9Kq"}, []string{"join", "sync.example.com/7f3a9c21-Ab3xY9Kq"}},
	}
	for name, c := range cases {
		if got := syncArgs(c.in); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: syncArgs(%q) = %q, want %q", name, c.in, got, c.want)
		}
	}
}
