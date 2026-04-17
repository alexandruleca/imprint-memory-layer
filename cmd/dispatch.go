package cmd

import "github.com/hunter/imprint/internal/output"

// setupTargets is the ordered list used by `imprint setup all`. Kept small
// and explicit — one handler per host AI tool.
var setupTargets = []string{"claude-code", "cursor", "codex", "copilot", "cline"}

// DispatchSetup routes a setup target name to the right handler. Returns
// false for unknown targets so the caller can report a usage error. The
// "all" target iterates every handler; each handler self-skips (warn +
// return) when its host tool isn't installed.
func DispatchSetup(target string) bool {
	switch target {
	case "claude-code", "claude":
		SetupClaudeCode()
	case "cursor":
		SetupCursor()
	case "codex":
		SetupCodex()
	case "copilot":
		SetupCopilot()
	case "cline":
		SetupCline()
	case "all":
		for _, t := range setupTargets {
			output.Header("─── " + t + " ───")
			DispatchSetup(t)
		}
	default:
		return false
	}
	return true
}
