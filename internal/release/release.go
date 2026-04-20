// Package release resolves Imprint releases from GitHub. Shared by
// `imprint update` and (future) `imprint version --check`.
package release

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"runtime"
	"strings"
	"time"
)

// Repo is the canonical GitHub repo path. Kept in one spot so install.sh
// and the updater stay in sync.
const Repo = "alexandruleca/imprint-memory-layer"

// Channel selects stable (latest non-prerelease) or dev (latest prerelease).
type Channel string

const (
	Stable Channel = "stable"
	Dev    Channel = "dev"
)

// Release is the subset of the GitHub release API we care about.
type Release struct {
	TagName     string         `json:"tag_name"`
	Name        string         `json:"name"`
	Body        string         `json:"body"`
	Prerelease  bool           `json:"prerelease"`
	PublishedAt time.Time      `json:"published_at"`
	Assets      []ReleaseAsset `json:"assets"`
}

// ReleaseAsset is a single downloadable artifact on a release.
type ReleaseAsset struct {
	Name               string `json:"name"`
	BrowserDownloadURL string `json:"browser_download_url"`
	Size               int64  `json:"size"`
}

// ArchiveName returns the platform/arch-specific archive name, matching
// install.sh / install.ps1's naming convention. Windows ships as .zip;
// Linux/macOS as .tar.gz.
func ArchiveName() string {
	osn := runtime.GOOS
	arch := runtime.GOARCH // amd64 | arm64
	ext := ".tar.gz"
	if osn == "windows" {
		ext = ".zip"
	}
	return fmt.Sprintf("imprint-%s-%s%s", osn, arch, ext)
}

// ArchiveIsZip reports whether the current platform's archive is a .zip
// (Windows) rather than a .tar.gz.
func ArchiveIsZip() bool { return runtime.GOOS == "windows" }

// ArchiveDirName returns the directory name the archive expands into —
// same stem as ArchiveName, no extension. Matches the release packager.
func ArchiveDirName() string {
	n := ArchiveName()
	n = strings.TrimSuffix(n, ".tar.gz")
	n = strings.TrimSuffix(n, ".zip")
	return n
}

// ResolveLatest hits the GitHub API and returns the newest release for the
// given channel. Stable uses /releases/latest (GitHub-curated). Dev scans
// /releases?per_page=30 for the newest prerelease.
func ResolveLatest(ch Channel) (*Release, error) {
	if ch == Stable {
		return fetchRelease(fmt.Sprintf("https://api.github.com/repos/%s/releases/latest", Repo))
	}
	rels, err := fetchReleases(fmt.Sprintf("https://api.github.com/repos/%s/releases?per_page=30", Repo))
	if err != nil {
		return nil, err
	}
	for _, r := range rels {
		if r.Prerelease {
			rr := r
			return &rr, nil
		}
	}
	return nil, fmt.Errorf("no prerelease found in the 30 most recent releases")
}

// ResolveTag fetches a specific tag. Returns 404 wrapped as a clear error.
func ResolveTag(tag string) (*Release, error) {
	tag = strings.TrimSpace(tag)
	if tag == "" {
		return nil, fmt.Errorf("empty tag")
	}
	return fetchRelease(fmt.Sprintf("https://api.github.com/repos/%s/releases/tags/%s", Repo, tag))
}

// FindArchiveAsset picks the current-platform archive asset from a release.
// Returns an error if no matching asset is attached.
func FindArchiveAsset(r *Release) (*ReleaseAsset, error) {
	want := ArchiveName()
	for i := range r.Assets {
		if r.Assets[i].Name == want {
			return &r.Assets[i], nil
		}
	}
	return nil, fmt.Errorf("release %s has no asset named %s", r.TagName, want)
}

// Download streams a URL into the writer. Caller is responsible for creating
// the destination and closing it. Uses http.DefaultClient with a 5 min
// per-request timeout — plenty for a tarball, strict enough to error out on
// stalled connections.
func Download(url string, w io.Writer) error {
	client := &http.Client{Timeout: 5 * time.Minute}
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/octet-stream")
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("GET %s: %w", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("GET %s: HTTP %d", url, resp.StatusCode)
	}
	if _, err := io.Copy(w, resp.Body); err != nil {
		return fmt.Errorf("reading %s: %w", url, err)
	}
	return nil
}

func fetchRelease(url string) (*Release, error) {
	body, err := apiGET(url)
	if err != nil {
		return nil, err
	}
	var r Release
	if err := json.Unmarshal(body, &r); err != nil {
		return nil, fmt.Errorf("parsing release JSON: %w", err)
	}
	return &r, nil
}

func fetchReleases(url string) ([]Release, error) {
	body, err := apiGET(url)
	if err != nil {
		return nil, err
	}
	var rs []Release
	if err := json.Unmarshal(body, &rs); err != nil {
		return nil, fmt.Errorf("parsing releases JSON: %w", err)
	}
	return rs, nil
}

func apiGET(url string) ([]byte, error) {
	client := &http.Client{Timeout: 30 * time.Second}
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("User-Agent", "imprint-update")
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("GET %s: %w", url, err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == 404 {
		return nil, fmt.Errorf("not found: %s", url)
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("GET %s: HTTP %d — %s", url, resp.StatusCode, strings.TrimSpace(string(body)))
	}
	return body, nil
}
