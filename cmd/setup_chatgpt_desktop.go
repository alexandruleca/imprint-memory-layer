package cmd

import (
	"strings"

	"github.com/hunter/imprint/internal/instructions"
	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
)

// SetupChatGPTDesktop reports ChatGPT Desktop's MCP status. The desktop app
// wires MCP via remote "connectors" (SSE over HTTPS) rather than a local
// stdio config, so there's no config file to patch for a local-first
// server like Imprint. The handler detects the install, prints a paste-
// ready Custom-Instructions snippet (since ChatGPT Desktop's tool-use
// nudges have to go in Settings → Personalization → Custom Instructions),
// and returns without writing any config file.
//
// WSL2-aware: checks the Windows-side install location when running inside
// WSL. When OpenAI ships a local-stdio MCP config path, upgrade this
// handler the way SetupClaudeDesktop works.
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
	setupHostsRan++
	output.Info("ChatGPT Desktop wires MCP via in-app Connectors (hosted SSE), not a local stdio config.")
	output.Info("To consume Imprint: expose it via a reverse proxy with SSE (e.g. `supergateway`) and add the public URL under Settings → Connectors.")
	output.Info("Meanwhile, paste the following into Settings → Personalization → Custom Instructions so ChatGPT reaches for the imprint tools first:")
	printIndentedBlock(instructions.DesktopProfileSnippet)
}

// printIndentedBlock writes a multi-line string indented two spaces so it
// stands out from the surrounding setup log without breaking terminal wrap.
func printIndentedBlock(s string) {
	for _, line := range strings.Split(strings.TrimRight(s, "\n"), "\n") {
		output.Info("  " + line)
	}
}
