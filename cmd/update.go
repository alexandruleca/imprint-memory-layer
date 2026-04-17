package cmd

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"path/filepath"
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

	tarball := filepath.Join(tmp, asset.Name)
	output.Info("Downloading " + asset.BrowserDownloadURL)
	if err := downloadTo(tarball, asset.BrowserDownloadURL); err != nil {
		output.Fail(err.Error())
	}

	output.Info("Extracting archive...")
	if err := runner.Run("tar", "-xzf", tarball, "-C", tmp); err != nil {
		output.Fail("Failed to extract archive: " + err.Error())
	}
	extracted := filepath.Join(tmp, release.ArchiveDirName())
	if !platform.DirExists(extracted) {
		output.Fail("Unexpected archive layout: " + extracted + " not found")
	}

	// Back up the current binary so the user can roll back manually if the
	// rsync breaks something weird. A single slot is enough — we don't want
	// to accumulate backups indefinitely.
	binPath := filepath.Join(installDir, "bin", "imprint")
	if platform.FileExists(binPath) {
		_ = os.Rename(binPath, binPath+".prev")
	}

	output.Info("Updating files in " + installDir + " (preserving data/ + .venv/)...")
	// --delete-during drops stale files from the previous release; excluded
	// paths still survive because rsync never descends into them.
	if err := runner.Run("rsync", "-a", "--delete-during",
		"--exclude", "data/", "--exclude", ".venv/",
		extracted+"/", installDir+"/"); err != nil {
		output.Fail("rsync failed: " + err.Error() + " (previous binary at " + binPath + ".prev)")
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
