package platform

import "testing"

func withWSL(t *testing.T, enabled bool, fn func()) {
	t.Helper()
	prev := isWSLOverride
	v := enabled
	isWSLOverride = &v
	t.Cleanup(func() { isWSLOverride = prev })
	fn()
}

func TestTranslateWSLPath_NonWSL(t *testing.T) {
	withWSL(t, false, func() {
		cases := []string{
			`C:\Users\alex`,
			`C:/Users/alex`,
			`\\wsl$\Ubuntu\home\alex`,
			`/home/alex`,
			``,
		}
		for _, in := range cases {
			if got := TranslateWSLPath(in); got != in {
				t.Errorf("non-WSL should pass through unchanged: in=%q got=%q", in, got)
			}
		}
	})
}

func TestTranslateWSLPath_WSL(t *testing.T) {
	withWSL(t, true, func() {
		cases := []struct {
			in, want string
		}{
			// Drive-letter, backslash
			{`C:\Users\alex`, `/mnt/c/Users/alex`},
			{`C:\Users\alex\`, `/mnt/c/Users/alex/`},
			{`D:\projects\foo\bar.txt`, `/mnt/d/projects/foo/bar.txt`},
			// Drive-letter, forward slash
			{`C:/Users/alex`, `/mnt/c/Users/alex`},
			{`e:/path/to/thing`, `/mnt/e/path/to/thing`},
			// Lowercase drive letter normalizes
			{`c:\foo`, `/mnt/c/foo`},
			// Mixed separators within the tail
			{`C:\foo/bar\baz`, `/mnt/c/foo/bar/baz`},
			// WSL UNC paths from Windows Explorer
			{`\\wsl$\Ubuntu\home\alex`, `/home/alex`},
			{`\\wsl.localhost\Ubuntu-22.04\home\alex\file`, `/home/alex/file`},
			// Already-WSL paths: unchanged
			{`/mnt/c/Users/alex`, `/mnt/c/Users/alex`},
			{`/home/alex/project`, `/home/alex/project`},
			// Non-path strings: unchanged
			{`relative/thing`, `relative/thing`},
			{`https://example.com/foo`, `https://example.com/foo`},
			{``, ``},
			// Bare drive letter without separator (don't match — ambiguous)
			{`C:`, `C:`},
		}
		for _, c := range cases {
			got := TranslateWSLPath(c.in)
			if got != c.want {
				t.Errorf("TranslateWSLPath(%q) = %q, want %q", c.in, got, c.want)
			}
		}
	})
}
