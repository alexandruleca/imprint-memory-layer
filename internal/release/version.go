package release

import (
	"cmp"
	"regexp"
	"strconv"
)

// versionPattern matches Imprint's release versions: vMAJOR.MINOR.PATCH for a
// stable release and vMAJOR.MINOR.PATCH-dev.N for the Nth dev prerelease of
// that coming stable release (ADR 0066). A build from a checkout carries
// `git describe`'s -<commits>-g<hash> and -dirty after the release it
// describes, and ranks as that release.
var versionPattern = regexp.MustCompile(`^v?(\d+)\.(\d+)\.(\d+)(?:-dev\.(\d+))?(?:-\d+-g[0-9a-f]+)?(?:-dirty)?$`)

type version struct {
	core   [3]int
	stable bool
	build  int // N of a -dev.N prerelease
}

func parseVersion(s string) (version, bool) {
	m := versionPattern.FindStringSubmatch(s)
	if m == nil {
		return version{}, false
	}
	var v version
	for i := range v.core {
		n, err := strconv.Atoi(m[i+1])
		if err != nil {
			return version{}, false
		}
		v.core[i] = n
	}
	v.stable = m[4] == ""
	if !v.stable {
		n, err := strconv.Atoi(m[4])
		if err != nil {
			return version{}, false
		}
		v.build = n
	}
	return v, true
}

// compare orders versions by semantic version precedence: a stable release
// follows every prerelease of it.
func (v version) compare(o version) int {
	for i := range v.core {
		if c := cmp.Compare(v.core[i], o.core[i]); c != 0 {
			return c
		}
	}
	if v.stable != o.stable {
		if v.stable {
			return 1
		}
		return -1
	}
	return cmp.Compare(v.build, o.build)
}

// Newer reports whether release tag a is a later version than b. A build
// without a release version ("dev", a bare commit hash) is never behind.
func Newer(a, b string) bool {
	va, okA := parseVersion(a)
	vb, okB := parseVersion(b)
	return okA && okB && va.compare(vb) > 0
}

// latestOnDev picks the dev channel's release: the highest version, stable
// or prerelease. dev always holds main's commits, and a commit main released
// is not built again as a prerelease, so a stable release newer than every
// prerelease is the dev channel's latest build too (ADR 0066).
func latestOnDev(releases []Release) (*Release, bool) {
	var latest *Release
	var latestVersion version
	for i := range releases {
		v, ok := parseVersion(releases[i].TagName)
		if ok && (latest == nil || v.compare(latestVersion) > 0) {
			latest, latestVersion = &releases[i], v
		}
	}
	return latest, latest != nil
}
