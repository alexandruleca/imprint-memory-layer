package instructions

import "strings"

// ImprintBase is the shared usage guidance installed for every supported
// AI tool target (Claude Code CLAUDE.md, Cursor rules, etc.). One source of
// truth so the contract stays consistent across tools.
const ImprintBase = `# Imprint Memory — Check Memory First

An Imprint MCP server is registered globally. It contains indexed code chunks, decisions, patterns, and project knowledge from past sessions.

## Every conversation
1. Call mcp__imprint__search with your first question — it auto-loads session context (wake_up) on the first call
2. Trust search results when confidence is high:
   - "High-confidence results" → answer directly, do NOT read files
   - No prefix → results are adequate, read files only if answer is incomplete
   - "Low-confidence matches" → search was insufficient, fall back to Read/Grep
3. Only fall back to Read/Grep when search returns no results, you need exact current content for edits, or the user asks
4. Pagination: search defaults to 10 results. If output says "use offset=N", call search again with that offset. Increase limit for broader results. Use file_chunks to expand truncated content.

## During conversation — store what you learn
- Architectural decisions and WHY they were made
- Bug root causes and how they were fixed
- Project conventions and patterns
- User corrections and preferences

## Do NOT store
- Raw file contents (already indexed)
- Temporary debugging state
- Things derivable from git history
`

// ClaudeCodeCLAUDE wraps the shared block with the Claude Code global header.
const ClaudeCodeCLAUDE = "# Global Instructions\n\n" + ImprintBase

// CursorRule wraps the shared block with Cursor MDC frontmatter so the rule
// is always applied (no glob restriction).
const CursorRule = `---
description: Imprint MCP — check memory first before reading files
alwaysApply: true
---

` + ImprintBase

// ClineRule is a plain-markdown rule file written under ~/.clinerules/.
// Cline reads every .md file in that dir as an always-on rule, so no
// frontmatter is needed — just a header to keep it distinguishable in
// Cline's UI when multiple rule files are installed.
const ClineRule = "# Imprint Memory\n\n" + ImprintBase

// CodexRule is merged into ~/.codex/AGENTS.md via managed markers so
// existing user-authored content is preserved. No frontmatter — AGENTS.md
// is plain markdown consumed by Codex CLI.
const CodexRule = ImprintBase

// CopilotRule is merged into the user-scope Copilot custom-instructions
// file via managed markers. GitHub Copilot reads the file as-is; no
// frontmatter.
const CopilotRule = ImprintBase

// DesktopProfileSnippet is the short, paste-friendly version for clients
// that don't honor the MCP handshake ``instructions`` field — typically the
// consumer desktop apps (Claude Desktop "Styles", ChatGPT Desktop "Custom
// Instructions"). Kept tight (<700 chars) so it fits in per-profile input
// boxes that clip long text.
const DesktopProfileSnippet = `Imprint MCP is connected. It's a semantic memory index over my prior projects, decisions, bugs, and patterns.

For any non-trivial question:
1. Call ` + "`search`" + ` from the imprint server FIRST — before reading files or guessing.
2. "High-confidence results" → answer from them directly. "Low-confidence" → fall back to other tools.
3. Use ` + "`neighbors`" + ` or ` + "`graph_scope`" + ` to explore around a hit. Use ` + "`file_chunks`" + ` to expand truncated chunks.
4. After the user accepts a decision/pattern/fix, call ` + "`store`" + ` (or ` + "`ingest_content`" + ` for bulkier payloads). Include the WHY, not just the WHAT.

Never skip search "just to be safe" — that's the memory. Skipping defeats the point.`

// MarkerStart and MarkerEnd delimit the managed Imprint section in rule
// files so re-running setup replaces only our block instead of clobbering
// unrelated user content (used for ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md,
// Copilot instructions).
const (
	MarkerStart = "<!-- imprint:begin -->"
	MarkerEnd   = "<!-- imprint:end -->"
)

// MergeManaged swaps the marker-bracketed block in `existing` with
// `managed`. If no markers are present, appends the managed block (with a
// blank-line separator) so prior content is preserved. Caller is
// responsible for wrapping `managed` in MarkerStart/MarkerEnd.
func MergeManaged(existing, managed string) string {
	startIdx := strings.Index(existing, MarkerStart)
	endIdx := strings.Index(existing, MarkerEnd)
	if startIdx >= 0 && endIdx > startIdx {
		endIdx += len(MarkerEnd)
		if endIdx < len(existing) && existing[endIdx] == '\n' {
			endIdx++
		}
		return existing[:startIdx] + managed + existing[endIdx:]
	}
	if existing == "" {
		return managed
	}
	if !strings.HasSuffix(existing, "\n") {
		existing += "\n"
	}
	return existing + "\n" + managed
}
