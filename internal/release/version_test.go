package release

import "testing"

func TestNewer(t *testing.T) {
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
		// A dev prerelease precedes the stable release it leads to, and later
		// dev builds of the same version follow earlier ones (ADR 0066).
		{"v0.25.0", "v0.25.0-dev.3", true},
		{"v0.25.0-dev.3", "v0.25.0", false},
		{"v0.25.0-dev.4", "v0.25.0-dev.3", true},
		{"v0.25.0-dev.10", "v0.25.0-dev.9", true},
		{"v0.25.0-dev.1", "v0.24.1", true},
		{"v0.24.1", "v0.25.0-dev.1", false},
		{"v0.25.0", "v0.25.0-dev.3-2-gabc1234-dirty", true},
		{"v0.25.0-dev.3", "v0.25.0-dev.3-2-gabc1234", false},
		{"v0.25.0-rc.1", "v0.24.0", false},
	}
	for _, c := range cases {
		if got := Newer(c.latest, c.current); got != c.want {
			t.Errorf("Newer(%q, %q) = %v, want %v", c.latest, c.current, got, c.want)
		}
	}
}

func TestLatestOnDevIsTheHighestVersionOfEitherKind(t *testing.T) {
	cases := []struct {
		name string
		tags []string
		pre  []bool
		want string
	}{
		{"a dev build ahead of stable", []string{"v0.25.0-dev.2", "v0.25.0-dev.1", "v0.24.0"}, []bool{true, true, false}, "v0.25.0-dev.2"},
		// dev was fast-forwarded to main's release; no prerelease was built for it.
		{"stable caught up with dev", []string{"v0.25.0", "v0.25.0-dev.2", "v0.24.0"}, []bool{false, true, false}, "v0.25.0"},
		// Pre-ADR 0066 dev builds were numbered after the last stable release.
		{"historical prereleases rank below their stable", []string{"v0.24.0", "v0.8.0-dev.76", "v0.8.0"}, []bool{false, true, false}, "v0.24.0"},
		// A stable hotfix created after a dev build of a later version.
		{"creation order is not version order", []string{"v0.25.1", "v0.26.0-dev.1", "v0.25.0"}, []bool{false, true, false}, "v0.26.0-dev.1"},
		{"tags outside the scheme are ignored", []string{"nightly", "v0.25.0-dev.1"}, []bool{true, true}, "v0.25.0-dev.1"},
	}
	for _, c := range cases {
		releases := make([]Release, len(c.tags))
		for i, tag := range c.tags {
			releases[i] = Release{TagName: tag, Prerelease: c.pre[i]}
		}
		got, ok := latestOnDev(releases)
		if !ok || got.TagName != c.want {
			t.Errorf("%s: latestOnDev = %v, want %s", c.name, got, c.want)
		}
	}
	if _, ok := latestOnDev([]Release{{TagName: "nightly"}}); ok {
		t.Error("latestOnDev chose a release without a version")
	}
}
