package cmd

import (
	"archive/tar"
	"archive/zip"
	"bufio"
	"compress/gzip"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/queuelock"
	"github.com/hunter/imprint/internal/release"
	"github.com/hunter/imprint/internal/runner"
)

// updateFlags holds the parsed CLI flags for `imprint update`.
type updateFlags struct {
	Version string
	Dev     bool
	Yes     bool
	Check   bool
	JSON    bool
}

// installKind is how this installation was set up, which decides how it
// updates (ADR 0023).
type installKind string

const (
	// installArchive: install.sh / install.ps1. Overlay the release archive.
	installArchive installKind = "archive"
	// installWindowsSetup: the Inno Setup installer. Run the new setup.exe,
	// which closes the app, replaces its files and reopens it.
	installWindowsSetup installKind = "windows-setup"
	// installMacPkg: the root-owned /Applications/Imprint.app. Open the new
	// .pkg so Installer asks for permission.
	installMacPkg installKind = "macos-pkg"
)

type installation struct {
	dir  string
	kind installKind
}

// updateReport is `imprint update --check --json`, read by the dashboard.
type updateReport struct {
	Current         string      `json:"current"`
	Latest          string      `json:"latest"`
	Name            string      `json:"name"`
	URL             string      `json:"url"`
	PublishedAt     time.Time   `json:"published_at"`
	UpdateAvailable bool        `json:"update_available"`
	Method          installKind `json:"method,omitempty"`
	// Unsupported says why this installation cannot update itself.
	Unsupported string `json:"unsupported,omitempty"`
}

// Update implements `imprint update`. It resolves the release, verifies the
// download against SHA256SUMS, and installs it the way this installation
// was set up: overlaying the archive (preserving data/ and .venv/), or
// handing over to the platform installer.
//
//	imprint update                  # latest stable, interactive confirm
//	imprint update --version vX.Y.Z # pinned release
//	imprint update --dev            # latest dev build (ADR 0066)
//	imprint update -y               # skip confirm
//	imprint update --check [--json] # print current + latest and exit
func Update(args []string, currentVersion string) {
	flags, err := parseUpdateFlags(args)
	if err != nil {
		fmt.Fprintln(os.Stderr, err.Error())
		os.Exit(2)
	}

	inst, instErr := resolveInstallation()

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
		printUpdateCheck(flags.JSON, currentVersion, target, inst, instErr)
		return
	}
	if instErr != nil {
		output.Fail(instErr.Error())
	}

	if target.TagName == currentVersion && currentVersion != "dev" {
		output.Skip(fmt.Sprintf("Already on %s — nothing to update", currentVersion))
		return
	}

	// A running ingest would lose its files and its server mid-job.
	dataDir := platform.DataDir(inst.dir)
	lock, err := queuelock.TryAcquire(dataDir, "update")
	if err != nil {
		output.Fail(err.Error() + " — update when it finishes")
	}
	defer lock.Release()

	switch inst.kind {
	case installWindowsSetup:
		updateWithWindowsSetup(inst, target, currentVersion, flags.Yes)
	case installMacPkg:
		updateWithMacPkg(inst, target, currentVersion, flags.Yes)
	default:
		updateArchive(inst, target, currentVersion, flags.Yes)
	}
}

func printUpdateCheck(asJSON bool, currentVersion string, target *release.Release, inst installation, instErr error) {
	report := updateReport{
		Current:         currentVersion,
		Latest:          target.TagName,
		Name:            target.Name,
		URL:             target.HTMLURL,
		PublishedAt:     target.PublishedAt,
		UpdateAvailable: release.Newer(target.TagName, currentVersion),
		Method:          inst.kind,
	}
	if instErr != nil {
		report.Unsupported = instErr.Error()
	}
	if asJSON {
		_ = json.NewEncoder(os.Stdout).Encode(report)
		return
	}
	fmt.Printf("Current: %s\nLatest:  %s   (%s)\n", currentVersion, target.TagName, target.Name)
	if report.Unsupported != "" {
		fmt.Println("Update:  not available here — " + report.Unsupported)
	} else {
		fmt.Println("Install: " + installLabel(inst.kind))
	}
	if strings.TrimSpace(target.Body) != "" {
		lines := strings.Split(strings.TrimSpace(target.Body), "\n")
		for i, l := range lines {
			if i >= 5 {
				break
			}
			fmt.Println("  " + l)
		}
	}
}

// updateArchive overlays the release archive onto an install.sh /
// install.ps1 tree, re-runs setup and restarts a running dashboard server.
func updateArchive(inst installation, target *release.Release, currentVersion string, yes bool) {
	installDir := inst.dir
	asset, err := release.FindArchiveAsset(target)
	if err != nil {
		output.Fail(err.Error())
	}

	fmt.Printf("\n  Current: %s\n  New:     %s\n  Archive: %s\n  Install: %s\n\n",
		currentVersion, target.TagName, asset.Name, installDir)
	fmt.Println("  Will update: bin/, imprint/, cmd/, requirements.txt (and rest of source tree)")
	fmt.Println("  Preserved:   data/ (workspaces, qdrant, sqlite, config, gpu_state), .venv/")
	fmt.Println()

	if !yes && !confirm(fmt.Sprintf("Update to %s?", target.TagName)) {
		output.Info("Aborted.")
		return
	}

	tmp, err := os.MkdirTemp("", "imprint-update-*")
	if err != nil {
		output.Fail("Could not create temp dir: " + err.Error())
	}
	defer os.RemoveAll(tmp)

	archivePath := downloadVerified(target, asset, tmp)

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

	// Keep the current binaries one step back for a manual rollback. Moving
	// them also frees the names of running executables, which Windows will
	// not overwrite (this updater, an open desktop window).
	moveBinariesAside(filepath.Join(installDir, "bin"))

	output.Info("Updating files in " + installDir + " (preserving data/ + .venv/ + python/ + .uv-cache/)...")
	if err := overlayCopy(extracted, installDir, []string{"data", ".venv", "python", ".uv-cache"}); err != nil {
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
	restartUI(binPath)
}

// updateWithWindowsSetup hands over to the release's Inno Setup installer.
// It must outlive this process, whose binary it replaces. Once it has
// provisioned the release, imprint-setup.ps1 restarts a running dashboard
// server, and the installer reopens Imprint.
func updateWithWindowsSetup(inst installation, target *release.Release, currentVersion string, yes bool) {
	asset, err := release.FindAsset(target, "imprint-windows-amd64-setup.exe")
	if err != nil {
		output.Fail(err.Error())
	}
	fmt.Printf("\n  Current:   %s\n  New:       %s\n  Installer: %s\n  Install:   %s\n\n",
		currentVersion, target.TagName, asset.Name, inst.dir)
	fmt.Println("  The installer runs in the background; the dashboard restarts when it finishes.")
	fmt.Println("  Preserved: data/ (workspaces, qdrant, sqlite, config), .venv/, your install profile")
	fmt.Println()
	if !yes && !confirm(fmt.Sprintf("Update to %s?", target.TagName)) {
		output.Info("Aborted.")
		return
	}

	dir := filepath.Join(os.TempDir(), "imprint-update-"+target.TagName)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		output.Fail("Could not create temp dir: " + err.Error())
	}
	installer := downloadVerified(target, asset, dir)

	logPath := filepath.Join(platform.DataDir(inst.dir), "update-setup.log")
	args := []string{"/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1", "/LOG=" + logPath}
	if !spawnDetached(installer, args) {
		output.Fail("Could not start the installer: " + installer)
	}
	output.Success("Installer started: it updates Imprint to " + target.TagName + " and restarts the dashboard.")
	output.Info("Installer log: " + logPath)
}

// updateWithMacPkg opens the release's .pkg in Installer, which asks for
// permission to replace the root-owned app bundle, then restarts a running
// dashboard server with the installed binary.
func updateWithMacPkg(inst installation, target *release.Release, currentVersion string, yes bool) {
	asset, err := release.FindAsset(target, fmt.Sprintf("imprint-darwin-%s.pkg", runtime.GOARCH))
	if err != nil {
		output.Fail(err.Error())
	}
	fmt.Printf("\n  Current:   %s\n  New:       %s\n  Installer: %s\n  Install:   %s\n\n",
		currentVersion, target.TagName, asset.Name, inst.dir)
	fmt.Println("  Installer opens and asks for your password. Your data stays in ~/.local/share/imprint.")
	fmt.Println()
	if !yes && !confirm(fmt.Sprintf("Update to %s?", target.TagName)) {
		output.Info("Aborted.")
		return
	}

	tmp, err := os.MkdirTemp("", "imprint-update-*")
	if err != nil {
		output.Fail("Could not create temp dir: " + err.Error())
	}
	defer os.RemoveAll(tmp)
	pkg := downloadVerified(target, asset, tmp)

	output.Info("Opening the installer — follow its steps")
	if err := runner.Run("open", "-W", pkg); err != nil {
		output.Fail("Could not open the installer: " + err.Error())
	}

	binPath := filepath.Join(inst.dir, "bin", "imprint")
	installed, err := runner.RunCapture(binPath, "version")
	if err != nil || strings.TrimSpace(strings.TrimPrefix(installed, "imprint ")) != target.TagName {
		output.Fail("Installer closed without installing " + target.TagName)
	}
	output.Success("Updated to " + target.TagName)
	restartUI(binPath)
}

// downloadVerified downloads a release asset into dir and checks it against
// SHA256SUMS before anything is installed.
func downloadVerified(target *release.Release, asset *release.ReleaseAsset, dir string) string {
	path := filepath.Join(dir, asset.Name)
	output.Info("Downloading " + asset.BrowserDownloadURL)
	if err := downloadTo(path, asset.BrowserDownloadURL); err != nil {
		output.Fail(err.Error())
	}
	if err := release.VerifyReleaseArchive(target, asset.Name, path); err != nil {
		output.Fail("Download verification failed: " + err.Error())
	}
	return path
}

// restartUI restarts the running dashboard server, whoever started it, so it
// serves the installed code (ADR 0050). The installed binary does it.
func restartUI(binPath string) {
	output.Info("Restarting the dashboard server...")
	if err := runner.Run(binPath, "ui", "restart"); err != nil {
		output.Warn("Dashboard restart failed: " + err.Error() + " — close and reopen Imprint")
	}
}

// moveBinariesAside renames each file in bin/ to <name>.prev, replacing the
// previous backup.
func moveBinariesAside(binDir string) {
	entries, err := os.ReadDir(binDir)
	if err != nil {
		return
	}
	for _, entry := range entries {
		if !entry.Type().IsRegular() || strings.HasSuffix(entry.Name(), ".prev") {
			continue
		}
		path := filepath.Join(binDir, entry.Name())
		_ = os.Remove(path + ".prev")
		_ = os.Rename(path, path+".prev")
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
		case a == "--json":
			f.JSON = true
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
	if f.JSON && !f.Check {
		return f, fmt.Errorf("--json only applies to --check")
	}
	return f, nil
}

func printUpdateHelp() {
	fmt.Print(`imprint update — upgrade to a newer release

Usage:
  imprint update                   # latest stable (prompts for confirmation)
  imprint update --version vX.Y.Z  # pin a specific release tag
  imprint update --dev             # latest dev build: the newest dev prerelease,
                                   # or the stable release once it is newer
  imprint update --check           # show current + latest and exit
  imprint update --check --json    # the same as JSON, for the dashboard
  imprint update -y | --yes        # skip confirmation prompt

How it installs depends on how Imprint was installed:
  install.sh / install.ps1   replace the install tree, re-run setup and
                             restart a running dashboard server
  Windows installer          run the new setup.exe silently; it restarts a
                             running dashboard server and reopens Imprint
  macOS .pkg                 open the new .pkg in Installer, then restart a
                             running dashboard server

Data preservation:
  data/   (workspaces, qdrant storage, sqlite graphs, config, gpu_state.json)
  .venv/  (Python virtual environment)
are never touched.
`)
}

// resolveInstallation finds the installation this binary belongs to and how
// it was installed. Containers and source checkouts are not installations
// that can replace themselves.
func resolveInstallation() (installation, error) {
	if platform.FileExists("/.dockerenv") || platform.FileExists("/run/.containerenv") {
		return installation{}, errors.New("running in a container: pull the new image and recreate the container (the /data volume is kept)")
	}
	dir, err := resolveInstallDir()
	if err != nil {
		return installation{}, err
	}
	if platform.DirExists(filepath.Join(dir, ".git")) {
		return installation{}, fmt.Errorf("%s is a source checkout: update it with git pull and rebuild", dir)
	}
	return installation{dir: dir, kind: installKindOf(dir, runtime.GOOS)}, nil
}

// installKindOf tells an installer-managed tree from an install.sh /
// install.ps1 one. Inno Setup leaves its uninstaller in the install
// directory; the macOS .pkg installs into the app bundle.
func installKindOf(dir, goos string) installKind {
	switch {
	case goos == "windows" && platform.FileExists(filepath.Join(dir, "unins000.exe")):
		return installWindowsSetup
	case goos == "darwin" && strings.HasSuffix(filepath.ToSlash(dir), ".app/Contents/Resources/imprint"):
		return installMacPkg
	}
	return installArchive
}

func installLabel(kind installKind) string {
	switch kind {
	case installWindowsSetup:
		return "Windows installer — updates by running the new setup.exe"
	case installMacPkg:
		return "macOS .pkg — updates by opening the new .pkg"
	}
	return "install script — updates in place"
}

// resolveInstallDir returns the parent of bin/ for the running binary —
// typically ~/.local/share/imprint/. Refuses a go-run / source build where
// there is no bin/imprint.
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

// confirm asks a y/N question on the terminal, so it works even when stdin
// is piped (e.g. wrapping `imprint update` inside a shell pipeline).
// Defaults to "no" on blank input to stay conservative with destructive ops.
func confirm(prompt string) bool {
	in, out := "/dev/tty", "/dev/tty"
	if runtime.GOOS == "windows" {
		in, out = "CONIN$", "CONOUT$"
	}
	tty, err := os.Open(in)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s Cannot open the terminal — pass -y to skip confirm.\n", prompt)
		return false
	}
	defer tty.Close()
	if w, err := os.OpenFile(out, os.O_WRONLY, 0); err == nil {
		fmt.Fprintf(w, "%s [y/N] ", prompt)
		w.Close()
	} else {
		fmt.Fprintf(os.Stderr, "%s [y/N] ", prompt)
	}
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
