package cmd

import (
	"os"
	"strings"

	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
)

// SetupChatGPTDesktop registers current native desktop versions through
// their shared Codex profile. WSL detection alone cannot make Linux
// interpreter paths usable in the Windows-side profile.
func SetupChatGPTDesktop() {
	marker := platform.ChatGPTDesktopInstallMarker()
	if marker == "" {
		output.Skip("ChatGPT Desktop: unsupported platform for detection.")
		return
	}
	if !platform.FileExists(marker) && !platform.DirExists(marker) {
		output.Skip("ChatGPT Desktop not detected at " + marker + ".")
		return
	}

	output.Success("ChatGPT Desktop detected at " + marker)
	if platform.IsWSL() {
		output.Warn("ChatGPT Desktop runs on Windows. Run the native Windows Imprint setup to register its local runtime; the Linux Codex profile is separate.")
		return
	}
	setupHostsRan++
	output.Info("Current ChatGPT desktop versions support local MCP servers and share the Codex config.toml registry.")
	if err := os.MkdirAll(platform.CodexHome(), 0755); err != nil {
		output.Fail("Could not create the shared MCP profile: " + err.Error())
		return
	}
	SetupCodex()
	output.Info("Restart ChatGPT desktop and check Settings → MCP servers for imprint.")
	output.Info("For versions without local MCP support, upgrade the app. See docs/integrations.md for compatibility details.")
	output.Info("Optional usage guidance for Custom Instructions:")
	printIndentedBlock(instructions.DesktopProfileSnippet)
	output.Info("Auto-sync past conversations: request a data export in chat.openai.com (Settings → Data controls → Export), then run `imprint learn --desktop`. The scanner picks up the zip from your Downloads folder.")
}

// printIndentedBlock writes a multi-line string indented two spaces so it
// stands out from the surrounding setup log without breaking terminal wrap.
func printIndentedBlock(s string) {
	for _, line := range strings.Split(strings.TrimRight(s, "\n"), "\n") {
		output.Info("  " + line)
	}
}
