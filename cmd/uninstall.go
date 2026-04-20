package cmd

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/jsonutil"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
)

// Uninstall fully removes imprint from the system: runs Disable to tear
// down MCP registrations + hooks across all hosts, strips the managed
// CLAUDE.md block, removes the shell alias, deletes the ~/.local/bin
// symlink, and (unless --keep-data) removes the venv, data directory,
// and install dir. Destructive — prompts for confirmation unless -y.
func Uninstall(args []string) {
	force := false
	keepData := false
	for _, a := range args {
		switch a {
		case "-y", "--yes", "--force":
			force = true
		case "--keep-data":
			keepData = true
		default:
			output.Fail("uninstall: unknown flag " + a + " (expected: -y | --force | --keep-data)")
		}
	}

	projectDir := platform.FindProjectDir()
	dataDir := platform.DataDir(projectDir)
	venvDir := platform.VenvDir(projectDir)
	installDir := filepath.Join(platform.HomeDir(), ".local", "share", "imprint")

	fmt.Println()
	output.Header("═══ Uninstalling Imprint ═══")
	fmt.Println()
	fmt.Println("  This will:")
	fmt.Println("    - stop the server, unregister MCP from every host, strip hooks")
	fmt.Println("    - strip the managed block from ~/.claude/CLAUDE.md")
	fmt.Println("    - remove the shell alias and ~/.local/bin/imprint symlink")
	if keepData {
		fmt.Println("    - remove .venv/  (keeping data at " + dataDir + ")")
	} else {
		fmt.Println("    - DELETE data/ at " + dataDir + " (all memories + configs)")
		fmt.Println("    - DELETE .venv/ at " + venvDir)
		if platform.DirExists(installDir) {
			fmt.Println("    - DELETE install dir at " + installDir)
		}
	}
	fmt.Println()

	if !force {
		fmt.Print("  Type 'yes' to continue: ")
		reply, _ := bufio.NewReader(os.Stdin).ReadString('\n')
		if strings.TrimSpace(reply) != "yes" {
			output.Info("Aborted.")
			return
		}
	}

	// 1. Reuse Disable: stops server, removes MCP from every host,
	//    strips hooks from Claude Code settings.json.
	Disable(nil)

	// 2. Drop the `mcp__imprint__*` permission entry from settings.json.
	removeClaudePermission(platform.ClaudeSettingsPath(), "mcp__imprint__*")

	// 3. Strip the managed imprint block from ~/.claude/CLAUDE.md.
	stripManagedClaudeMD()

	// 4. Remove the shell alias we appended during setup.
	removeShellAlias("imprint")

	// 5. Remove the ~/.local/bin/imprint symlink created by install.sh.
	removeSymlinkIfLink(filepath.Join(platform.HomeDir(), ".local", "bin", "imprint"))

	// 6. Delete venv + data + install dir.
	removeDirReport(venvDir, ".venv")
	if !keepData {
		removeDirReport(dataDir, "data dir")
		if installDir != projectDir && platform.DirExists(installDir) {
			// Separate install dir (user has both a source checkout and a
			// release install). Wipe its own venv + data too.
			removeDirReport(filepath.Join(installDir, ".venv"), "install .venv")
			removeDirReport(filepath.Join(installDir, "data"), "install data dir")
			removeDirReport(installDir, "install dir")
		} else if installDir == projectDir {
			// We ARE the install dir — fine to wipe in full.
			removeDirReport(installDir, "install dir")
		} else {
			output.Skip("Source checkout at " + projectDir + " left intact")
		}
	} else {
		output.Info("Keeping data at " + dataDir)
	}

	fmt.Println()
	output.Header("═══ Uninstalled ═══")
	fmt.Println()
	fmt.Println("  Restart your shell to drop the removed alias from the current session.")
	fmt.Println()
}

// removeClaudePermission drops a single entry from permissions.allow in
// settings.json. No-op if the file, the permissions block, or the entry
// is absent.
func removeClaudePermission(settingsPath, perm string) {
	if !platform.FileExists(settingsPath) {
		output.Skip("Claude settings not present (" + settingsPath + ")")
		return
	}
	data, err := jsonutil.ReadJSON(settingsPath)
	if err != nil {
		output.Warn("Could not read " + settingsPath + ": " + err.Error())
		return
	}
	permissions, ok := data["permissions"].(map[string]any)
	if !ok {
		output.Skip("No imprint permission in " + settingsPath)
		return
	}
	allow, ok := permissions["allow"].([]any)
	if !ok {
		output.Skip("No imprint permission in " + settingsPath)
		return
	}
	filtered := make([]any, 0, len(allow))
	removed := false
	for _, entry := range allow {
		if s, ok := entry.(string); ok && s == perm {
			removed = true
			continue
		}
		filtered = append(filtered, entry)
	}
	if !removed {
		output.Skip("No imprint permission in " + settingsPath)
		return
	}
	permissions["allow"] = filtered
	if err := jsonutil.WriteJSON(settingsPath, data); err != nil {
		output.Warn("Could not update " + settingsPath + ": " + err.Error())
		return
	}
	output.Success("Removed imprint permission from " + settingsPath)
}

// stripManagedClaudeMD removes the marker-bracketed imprint block from
// ~/.claude/CLAUDE.md while preserving any other user-authored content.
func stripManagedClaudeMD() {
	claudeMD := filepath.Join(platform.HomeDir(), ".claude", "CLAUDE.md")
	if !platform.FileExists(claudeMD) {
		output.Skip("Global CLAUDE.md not present")
		return
	}
	data, err := os.ReadFile(claudeMD)
	if err != nil {
		output.Warn("Could not read " + claudeMD + ": " + err.Error())
		return
	}
	existing := string(data)
	start := strings.Index(existing, instructions.MarkerStart)
	end := strings.Index(existing, instructions.MarkerEnd)
	if start < 0 || end < 0 || end <= start {
		output.Skip("No managed imprint block in " + claudeMD)
		return
	}
	end += len(instructions.MarkerEnd)
	if end < len(existing) && existing[end] == '\n' {
		end++
	}
	// Eat one leading blank line that setupGlobalClaudeMD may have inserted
	// before the managed block when appending to non-empty content.
	trimmed := start
	if trimmed >= 2 && existing[trimmed-1] == '\n' && existing[trimmed-2] == '\n' {
		trimmed--
	}
	updated := existing[:trimmed] + existing[end:]
	if err := os.WriteFile(claudeMD, []byte(updated), 0644); err != nil {
		output.Warn("Could not update " + claudeMD + ": " + err.Error())
		return
	}
	output.Success("Stripped managed block from " + claudeMD)
}

// removeShellAlias strips the `# imprint CLI alias` comment and the alias
// line that follows it. Matches the exact pair that setupShellAlias wrote.
func removeShellAlias(name string) {
	_, rcFile := platform.DetectShell()
	if rcFile == "" || !platform.FileExists(rcFile) {
		output.Skip("Shell RC file not present")
		return
	}
	data, err := os.ReadFile(rcFile)
	if err != nil {
		output.Warn("Could not read " + rcFile + ": " + err.Error())
		return
	}
	content := string(data)
	marker := "# " + name + " CLI alias"
	idx := strings.Index(content, marker)
	if idx < 0 {
		output.Skip("No imprint alias in " + rcFile)
		return
	}
	// Span = blank line before marker (if any) + marker line + alias line.
	end := idx + len(marker)
	if end < len(content) && content[end] == '\n' {
		end++
	}
	if nl := strings.Index(content[end:], "\n"); nl >= 0 {
		end += nl + 1
	} else {
		end = len(content)
	}
	start := idx
	if start > 0 && content[start-1] == '\n' {
		start--
	}
	updated := content[:start] + content[end:]
	if err := os.WriteFile(rcFile, []byte(updated), 0644); err != nil {
		output.Warn("Could not update " + rcFile + ": " + err.Error())
		return
	}
	output.Success("Removed imprint alias from " + rcFile)
}

// removeSymlinkIfLink removes a path only if it's a symlink. Leaves
// regular files alone — users sometimes copy the binary into PATH
// directly and we shouldn't delete that without being asked.
func removeSymlinkIfLink(path string) {
	info, err := os.Lstat(path)
	if err != nil {
		output.Skip("No symlink at " + path)
		return
	}
	if info.Mode()&os.ModeSymlink == 0 {
		output.Skip(path + " is not a symlink — leaving intact")
		return
	}
	if err := os.Remove(path); err != nil {
		output.Warn("Could not remove symlink " + path + ": " + err.Error())
		return
	}
	output.Success("Removed symlink " + path)
}

// removeDirReport is a thin os.RemoveAll wrapper that reports Skip/Success
// consistently with the rest of the uninstall output.
func removeDirReport(path, label string) {
	if !platform.DirExists(path) {
		output.Skip(label + " not present (" + path + ")")
		return
	}
	if err := os.RemoveAll(path); err != nil {
		output.Warn("Could not remove " + label + " " + path + ": " + err.Error())
		return
	}
	output.Success("Removed " + label + " at " + path)
}
