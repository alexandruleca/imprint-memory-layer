package cmd

import (
	"archive/tar"
	"archive/zip"
	"bufio"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/release"
	"github.com/hunter/imprint/internal/runner"
)

// updateFlags holds the parsed CLI flags for `imprint update`.
type updateFlags struct {
	Version string
	Dev     bool
	Yes     bool
	Check   bool
}

// Update implements `imprint update`. It downloads the selected release
// archive from GitHub, rsyncs the new code over the existing install
// (preserving data/ and .venv/), runs pip install -r requirements.txt, and
// re-runs `imprint setup` from the freshly-installed binary.
//
//	imprint update                  # latest stable, interactive confirm
//	imprint update --version vX.Y.Z # pinned release
//	imprint update --dev            # latest prerelease
//	imprint update -y               # skip confirm
//	imprint update --check          # print current + latest and exit
func Update(args []string, currentVersion string) {
	flags, err := parseUpdateFlags(args)
	if err != nil {
		fmt.Fprintln(os.Stderr, err.Error())
		os.Exit(2)
	}

	installDir, err := resolveInstallDir()
	if err != nil {
		output.Fail(err.Error())
	}

	channel := release.Stable
	if flags.Dev {
		channel = release.Dev
	}

	var target *release.Release
	switch {
	case flags.Version != "":
		target, err = release.ResolveTag(flags.Version)
	default:
		target, err = release.ResolveLatest(channel)
	}
	if err != nil {
		output.Fail("Could not resolve release: " + err.Error())
	}

	if flags.Check {
		fmt.Printf("Current: %s\nLatest:  %s   (%s)\n", currentVersion, target.TagName, target.Name)
		if strings.TrimSpace(target.Body) != "" {
			lines := strings.Split(strings.TrimSpace(target.Body), "\n")
			for i, l := range lines {
				if i >= 5 {
					break
				}
				fmt.Println("  " + l)
			}
		}
		return
	}

	if target.TagName == currentVersion && currentVersion != "dev" {
		output.Skip(fmt.Sprintf("Already on %s — nothing to update", currentVersion))
		return
	}

	asset, err := release.FindArchiveAsset(target)
	if err != nil {
		output.Fail(err.Error())
	}

	fmt.Printf("\n  Current: %s\n  New:     %s\n  Archive: %s\n  Install: %s\n\n",
		currentVersion, target.TagName, asset.Name, installDir)
	fmt.Println("  Will update: bin/, imprint/, cmd/, requirements.txt (and rest of source tree)")
	fmt.Println("  Preserved:   data/ (workspaces, qdrant, sqlite, config, gpu_state), .venv/")
	fmt.Println()

	if !flags.Yes && !confirm(fmt.Sprintf("Update to %s?", target.TagName)) {
		output.Info("Aborted.")
		return
	}

	tmp, err := os.MkdirTemp("", "imprint-update-*")
	if err != nil {
		output.Fail("Could not create temp dir: " + err.Error())
	}
	defer os.RemoveAll(tmp)

	archivePath := filepath.Join(tmp, asset.Name)
	output.Info("Downloading " + asset.BrowserDownloadURL)
	if err := downloadTo(archivePath, asset.BrowserDownloadURL); err != nil {
		output.Fail(err.Error())
	}

	output.Info("Extracting archive...")
	if err := extractArchive(archivePath, tmp); err != nil {
		output.Fail("Failed to extract archive: " + err.Error())
	}
	extracted := filepath.Join(tmp, release.ArchiveDirName())
	if !platform.DirExists(extracted) {
		output.Fail("Unexpected archive layout: " + extracted + " not found")
	}

	binName := "imprint"
	if runtime.GOOS == "windows" {
		binName = "imprint.exe"
	}
	binPath := filepath.Join(installDir, "bin", binName)
	// Back up the current binary so the user can roll back manually if the
	// overlay breaks something weird. A single slot is enough.
	if platform.FileExists(binPath) {
		_ = os.Rename(binPath, binPath+".prev")
	}

	installedVia := detectInstallMethod(installDir)
	if installedVia != "" {
		output.Info("Detected install layout: " + installedVia)
	}

	output.Info("Updating files in " + installDir + " (preserving data/ + .venv/)...")
	if err := overlayCopy(extracted, installDir, []string{"data", ".venv"}); err != nil {
		output.Fail("overlay copy failed: " + err.Error() + " (previous binary at " + binPath + ".prev)")
	}

	if platform.FileExists(binPath) {
		_ = os.Chmod(binPath, 0755)
	} else {
		output.Fail("Binary missing after update: " + binPath)
	}

	// Refresh ~/.local/bin/imprint symlink so `which imprint` still resolves.
	userBin := filepath.Join(platform.HomeDir(), ".local", "bin", "imprint")
	if err := os.MkdirAll(filepath.Dir(userBin), 0755); err == nil {
		_ = os.Remove(userBin)
		if err := os.Symlink(binPath, userBin); err == nil {
			output.Success("Symlink refreshed: " + userBin + " → " + binPath)
		}
	}

	// Install Python deps via the existing venv (install.sh keeps it across
	// upgrades, and that policy matches here).
	venvPip := platform.VenvBin(installDir, "pip")
	reqs := filepath.Join(installDir, "requirements.txt")
	if platform.FileExists(venvPip) && platform.FileExists(reqs) {
		output.Info("Installing Python dependencies...")
		if err := runner.Run(venvPip, "install", "-r", reqs, "--quiet"); err != nil {
			output.Warn("pip install failed: " + err.Error() + " — run `imprint setup` manually")
		}
	}

	output.Success("Updated to " + target.TagName)

	// Hand off to the new binary's setup so any new GPU/dependency logic
	// shipped in the release runs with up-to-date code.
	output.Info("Re-running setup with new binary...")
	if err := runner.Run(binPath, "setup"); err != nil {
		output.Warn("Setup returned error: " + err.Error())
	}
}

func parseUpdateFlags(args []string) (updateFlags, error) {
	var f updateFlags
	for i := 0; i < len(args); i++ {
		a := args[i]
		switch {
		case a == "--version" && i+1 < len(args):
			f.Version = args[i+1]
			i++
		case strings.HasPrefix(a, "--version="):
			f.Version = strings.TrimPrefix(a, "--version=")
		case a == "--dev":
			f.Dev = true
		case a == "--stable":
			f.Dev = false
		case a == "-y" || a == "--yes":
			f.Yes = true
		case a == "--check":
			f.Check = true
		case a == "-h" || a == "--help":
			printUpdateHelp()
			os.Exit(0)
		default:
			return f, fmt.Errorf("unknown flag: %s", a)
		}
	}
	if f.Version != "" && f.Dev {
		return f, fmt.Errorf("--version and --dev are mutually exclusive")
	}
	return f, nil
}

func printUpdateHelp() {
	fmt.Print(`imprint update — upgrade to a newer release

Usage:
  imprint update                   # latest stable (prompts for confirmation)
  imprint update --version vX.Y.Z  # pin a specific release tag
  imprint update --dev             # latest prerelease
  imprint update --check           # show current + latest and exit
  imprint update -y | --yes        # skip confirmation prompt

Data preservation:
  data/   (workspaces, qdrant storage, sqlite graphs, config, gpu_state.json)
  .venv/  (Python virtual environment)
are never touched. Everything else under the install directory is replaced.
`)
}

// resolveInstallDir returns the directory that `imprint update` should
// update. This is the parent of bin/ for the currently-running binary —
// typically ~/.local/share/imprint/. Refuses to update a go-run / source
// checkout where there is no bin/imprint.
func resolveInstallDir() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("resolving current executable: %w", err)
	}
	exe, err = filepath.EvalSymlinks(exe)
	if err != nil {
		return "", fmt.Errorf("resolving symlinks for %s: %w", exe, err)
	}
	installDir := filepath.Dir(filepath.Dir(exe))
	if filepath.Base(filepath.Dir(exe)) != "bin" {
		return "", fmt.Errorf(
			"imprint update is only supported for installs laid out like ~/.local/share/imprint/bin/imprint; "+
				"your binary is at %s — rebuild from source instead", exe)
	}
	if !platform.FileExists(filepath.Join(installDir, "requirements.txt")) {
		return "", fmt.Errorf(
			"install dir %s missing requirements.txt — refusing to update unknown layout", installDir)
	}
	return installDir, nil
}

// confirm asks a y/N question. Reads from /dev/tty so it works even when
// stdin is piped (e.g. wrapping `imprint update` inside a shell pipeline).
// Defaults to "no" on blank input to stay conservative with destructive ops.
func confirm(prompt string) bool {
	tty, err := os.OpenFile("/dev/tty", os.O_RDWR, 0)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s Cannot open /dev/tty — pass -y to skip confirm.\n", prompt)
		return false
	}
	defer tty.Close()
	fmt.Fprintf(tty, "%s [y/N] ", prompt)
	r := bufio.NewReader(tty)
	line, err := r.ReadString('\n')
	if err != nil && err != io.EOF {
		return false
	}
	ans := strings.ToLower(strings.TrimSpace(line))
	return ans == "y" || ans == "yes"
}

// downloadTo streams URL into path.
func downloadTo(path, url string) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return release.Download(url, f)
}

// extractArchive unpacks either a .zip (Windows releases) or a .tar.gz
// (Linux/macOS releases) into destDir. Uses the Go stdlib so we don't
// depend on `tar` / `unzip` being on PATH.
func extractArchive(archive, destDir string) error {
	if release.ArchiveIsZip() || strings.HasSuffix(strings.ToLower(archive), ".zip") {
		return extractZip(archive, destDir)
	}
	return extractTarGz(archive, destDir)
}

func extractZip(archive, destDir string) error {
	r, err := zip.OpenReader(archive)
	if err != nil {
		return fmt.Errorf("open zip: %w", err)
	}
	defer r.Close()

	for _, f := range r.File {
		target, err := safeJoin(destDir, f.Name)
		if err != nil {
			return err
		}
		if f.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		in, err := f.Open()
		if err != nil {
			return err
		}
		mode := f.Mode()
		if mode == 0 {
			mode = 0o644
		}
		out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
		if err != nil {
			in.Close()
			return err
		}
		if _, err := io.Copy(out, in); err != nil {
			in.Close()
			out.Close()
			return err
		}
		in.Close()
		out.Close()
	}
	return nil
}

func extractTarGz(archive, destDir string) error {
	f, err := os.Open(archive)
	if err != nil {
		return fmt.Errorf("open tar.gz: %w", err)
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return fmt.Errorf("gzip reader: %w", err)
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		target, err := safeJoin(destDir, h.Name)
		if err != nil {
			return err
		}
		switch h.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, os.FileMode(h.Mode)&0o777); err != nil {
				return err
			}
		case tar.TypeReg, tar.TypeRegA:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			mode := os.FileMode(h.Mode) & 0o777
			if mode == 0 {
				mode = 0o644
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
			if err != nil {
				return err
			}
			if _, err := io.Copy(out, tr); err != nil {
				out.Close()
				return err
			}
			out.Close()
		case tar.TypeSymlink:
			_ = os.Remove(target)
			if err := os.Symlink(h.Linkname, target); err != nil {
				return err
			}
		default:
			// Skip other entry types (xattrs, hardlinks from macOS tar, etc.)
		}
	}
	return nil
}

// safeJoin prevents zip/tar slip — reject paths that escape the destination.
func safeJoin(base, rel string) (string, error) {
	cleaned := filepath.Clean("/" + rel)
	joined := filepath.Join(base, cleaned)
	// Make sure joined is under base.
	baseAbs, err := filepath.Abs(base)
	if err != nil {
		return "", err
	}
	targetAbs, err := filepath.Abs(joined)
	if err != nil {
		return "", err
	}
	if !strings.HasPrefix(targetAbs+string(filepath.Separator), baseAbs+string(filepath.Separator)) {
		return "", fmt.Errorf("archive entry escapes destination: %s", rel)
	}
	return joined, nil
}

// overlayCopy walks src and copies every file into dst, creating parent dirs
// as needed. Directory names that match any entry in skipDirs (checked at
// the top level only) are left completely untouched — this is how we
// preserve the user's data/ and .venv/ directories across updates.
//
// Unlike rsync --delete-during, this does NOT prune stale files. Matches
// the install.ps1 (Expand-Archive -Force) semantics and is good enough
// for overlay updates; stale files from prior releases linger but don't
// break anything.
func overlayCopy(src, dst string, skipDirs []string) error {
	skip := make(map[string]bool, len(skipDirs))
	for _, d := range skipDirs {
		skip[d] = true
	}

	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		// Skip top-level directories we want to preserve in dst.
		parts := strings.Split(rel, string(filepath.Separator))
		if len(parts) > 0 && skip[parts[0]] {
			if info.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, info.Mode()&0o777)
		}
		if info.Mode()&os.ModeSymlink != 0 {
			link, err := os.Readlink(path)
			if err != nil {
				return err
			}
			_ = os.Remove(target)
			return os.Symlink(link, target)
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		in, err := os.Open(path)
		if err != nil {
			return err
		}
		defer in.Close()
		mode := info.Mode() & 0o777
		if mode == 0 {
			mode = 0o644
		}
		out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
		if err != nil {
			return err
		}
		if _, err := io.Copy(out, in); err != nil {
			out.Close()
			return err
		}
		return out.Close()
	})
}

// detectInstallMethod returns a short label describing how imprint appears
// to have been installed, or "" if we can't tell. Used only for user-facing
// messaging — the overlay update path is identical for all layouts.
func detectInstallMethod(installDir string) string {
	switch runtime.GOOS {
	case "darwin":
		if strings.Contains(installDir, "/Applications/Imprint.app/") {
			return "macOS .pkg (/Applications/Imprint.app)"
		}
	case "windows":
		lower := strings.ToLower(installDir)
		if strings.Contains(lower, `\programs\imprint`) || strings.Contains(lower, "/programs/imprint") {
			return "Windows installer (%LOCALAPPDATA%\\Programs\\Imprint)"
		}
	}
	if strings.Contains(installDir, ".local/share/imprint") || strings.Contains(installDir, `.local\share\imprint`) {
		return "curl/PowerShell installer (~/.local/share/imprint)"
	}
	return ""
}
