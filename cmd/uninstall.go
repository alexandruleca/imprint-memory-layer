package cmd

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/hunter/imprint/internal/configfile"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
)

// Uninstall disconnects clients, stops this installation's processes and
// removes its runtime and data. --keep-data keeps memories and settings for
// a later reinstall (ADR 0028).
func Uninstall(args []string) {
	force, keepData := false, false
	for _, a := range args {
		switch a {
		case "-y", "--yes", "--force":
			force = true
		case "--keep-data":
			keepData = true
		case "--delete-data":
			keepData = false
		default:
			output.Fail("uninstall: unknown flag " + a + " (expected: -y | --keep-data | --delete-data)")
		}
	}
	project := platform.FindProjectDir()
	data := platform.DataDir(project)
	output.Header("Uninstalling Imprint")
	fmt.Println("  Disconnect managed clients, stop every Imprint process and remove this installation's runtime.")
	if keepData {
		fmt.Println("  Memories preserved at " + data)
	} else {
		fmt.Println("  DELETE all memories and configuration at " + data + " (keep them with --keep-data)")
	}
	if !force {
		fmt.Print("  Type 'yes' to continue: ")
		reply, _ := bufio.NewReader(os.Stdin).ReadString('\n')
		if strings.TrimSpace(reply) != "yes" {
			output.Info("Aborted.")
			return
		}
	}
	// Disable fails before runtime deletion if any config or distro is inaccessible.
	Disable(nil)
	// The desktop shell, client-started MCP servers, jobs and Qdrant hold the
	// files open; clients are disconnected now, so they will not restart them.
	if err := stopOwnedProcesses(runtimeRoots(project), project); err != nil {
		output.Fail("Cleanup incomplete: " + err.Error() + ". Close them and retry.")
	}
	home := platform.HomeDir()
	_, detectedRC := platform.DetectShell()
	rcFiles := []string{detectedRC, filepath.Join(home, ".bashrc"), filepath.Join(home, ".zshrc"), filepath.Join(home, ".bash_profile"), filepath.Join(home, ".profile"), filepath.Join(home, ".config", "fish", "config.fish")}
	for _, rc := range rcFiles {
		if err := removeManagedAlias(rc, project); err != nil {
			output.Fail("Cleanup incomplete: " + err.Error())
		}
	}
	if err := removeOwnedSymlink(filepath.Join(home, ".local", "bin", "imprint"), project); err != nil {
		output.Fail(err.Error())
	}
	for _, dir := range []string{platform.VenvDir(project), platform.UvPythonInstallDir(project), platform.UvCacheDir(project)} {
		if err := os.RemoveAll(dir); err != nil {
			output.Fail("Could not remove runtime: " + err.Error())
		}
	}
	if !keepData {
		if err := os.RemoveAll(data); err != nil {
			output.Fail(err.Error())
		}
		// An app bundle keeps its runtime in a separate writable directory;
		// remove it once empty (a script install may share the path).
		if base := platform.MutableBaseDir(project); base != project {
			_ = os.Remove(base)
		}
	}
	// Never erase a separate release installation when called from a checkout.
	install := filepath.Join(home, ".local", "share", "imprint")
	if project == install && !keepData {
		// Windows cannot delete the running executable, but it can move it.
		if exe, err := os.Executable(); err == nil && pathWithin(exe, install) {
			_ = os.Rename(exe, filepath.Join(os.TempDir(), fmt.Sprintf("imprint-uninstalled-%d%s", os.Getpid(), filepath.Ext(exe))))
		}
		if err := os.RemoveAll(install); err != nil {
			output.Fail(err.Error())
		}
	}
	output.Success("Uninstalled. Restart client applications and your shell to refresh their configuration.")
}

// runtimeRoots are the directories every executable of this installation
// runs from: its binaries and desktop shell, the venv, the managed Python
// and data/ (Qdrant). A checkout's other tools stay out of reach.
func runtimeRoots(project string) []string {
	return []string{
		filepath.Join(project, "bin"),
		platform.VenvDir(project),
		platform.UvPythonInstallDir(project),
		platform.DataDir(project),
	}
}

func removeManagedAlias(file, project string) error {
	if file == "" {
		return nil
	}
	content, err := os.ReadFile(file)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	lines := strings.SplitAfter(string(content), "\n")
	var kept strings.Builder
	changed := false
	for i := 0; i < len(lines); i++ {
		if strings.TrimSpace(lines[i]) == "# imprint CLI alias" && i+1 < len(lines) && belongsToRuntime(strings.TrimSpace(lines[i+1]), project) {
			i++
			changed = true
			continue
		}
		kept.WriteString(lines[i])
	}
	if !changed {
		return nil
	}
	return configfile.Write(file, []byte(kept.String()))
}

func removeOwnedSymlink(file, project string) error {
	target, err := os.Readlink(file)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		if info, statErr := os.Lstat(file); statErr == nil && info.Mode()&os.ModeSymlink == 0 {
			return nil
		}
		return err
	}
	if !filepath.IsAbs(target) {
		target = filepath.Join(filepath.Dir(file), target)
	}
	if belongsToRuntime(filepath.Clean(target), project) {
		return os.Remove(file)
	}
	return nil
}
