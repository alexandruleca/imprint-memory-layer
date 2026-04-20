package cmd

import "github.com/hunter/imprint/internal/output"

// setupTargets is the ordered list used by `imprint setup all`. Kept small
// and explicit — one handler per host AI tool.
var setupTargets = []string{"claude-code", "cursor", "codex", "copilot", "cline", "openclaw"}

// setupHostsRan counts how many host handlers actually detected their tool
// and proceeded past the skip-on-missing guard. Handlers bump this after
// confirming their host is present. The "all" branch checks it to emit a
// "no autosetup targets found" message when every handler self-skipped.
var setupHostsRan int

// DispatchSetup routes a setup target name to the right handler. Returns
// false for unknown targets so the caller can report a usage error. The
// "all" target iterates every handler; each handler self-skips (warn +
// return) when its host tool isn't installed. If zero handlers ran, the
// caller sees a clear "no autosetup targets found" notice instead of a
// silent no-op.
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
	case "openclaw", "clawdbot", "moltbot":
		SetupOpenClaw()
	case "all":
		setupHostsRan = 0
		for _, t := range setupTargets {
			output.Header("─── " + t + " ───")
			DispatchSetup(t)
		}
		if setupHostsRan == 0 {
			output.Warn("No autosetup targets found — install Claude Code, Cursor, Codex, Copilot, Cline, or OpenClaw, then re-run `imprint setup`.")
		}
	default:
		return false
	}
	return true
}
