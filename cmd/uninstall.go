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

// Uninstall disconnects clients before removing this installation's runtime.
// Memories are kept unless --delete-data is explicitly requested.
func Uninstall(args []string) {
	force, keepData := false, true
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
	fmt.Println("  Disconnect managed clients, stop services and remove this installation's runtime.")
	if keepData {
		fmt.Println("  Memories preserved at " + data)
	} else {
		fmt.Println("  DELETE all memories and configuration at " + data)
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
	}
	// Never erase a separate release installation when called from a checkout.
	install := filepath.Join(home, ".local", "share", "imprint")
	if project == install && !keepData {
		if err := os.RemoveAll(install); err != nil {
			output.Fail(err.Error())
		}
	}
	output.Success("Uninstalled. Restart client applications and your shell to refresh their configuration.")
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
