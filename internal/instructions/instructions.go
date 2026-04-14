package instructions

// ImprintBase is the shared usage guidance installed for every supported
// AI tool target (Claude Code CLAUDE.md, Cursor rules, etc.). One source of
// truth so the contract stays consistent across tools.
const ImprintBase = `# Imprint Memory — Check Memory First

An Imprint MCP server is registered globally. It contains indexed code chunks, decisions, patterns, and project knowledge from past sessions.

## Every conversation
1. Call mcp__imprint__wake_up at the start to load prior context
2. Before answering questions about code, architecture, or project context — call mcp__imprint__search first
3. If search returns relevant results, use them to answer. The imprint memory contains actual code chunks — often enough for explanations without reading files
4. If the context from search isn't enough, or you need exact current file content for edits, read the files as needed

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

// MarkerStart and MarkerEnd delimit the managed Imprint section in
// ~/.claude/CLAUDE.md so we can replace it without nuking unrelated content.
const (
	MarkerStart = "<!-- imprint:begin -->"
	MarkerEnd   = "<!-- imprint:end -->"
)
