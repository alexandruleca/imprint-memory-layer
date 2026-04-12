# Knowledge Base — MemPalace

This repo hosts the MemPalace setup for persistent cross-project memory.

## IMPORTANT: Use MemPalace Before Reading Files

**At the start of every conversation**, call `mcp__mempalace__wake_up` to load prior context (~170 tokens). This avoids re-reading files and re-discovering things from previous sessions.

**Before reading files for context/background**, call `mcp__mempalace__search` first with a relevant query. If the palace has the answer, use it — don't re-read source files. Only fall back to Read/Grep when:
- The palace returns no results
- You need the *current* content of a specific file (live edits, line numbers)
- The user explicitly asks you to read a file

**During conversation**, store important findings:
- Architectural decisions and their reasoning
- Bug root causes and fixes
- Project patterns and conventions
- User preferences and corrections
- Cross-project relationships

**This saves tokens and API usage.** Re-reading files that were already discussed in past sessions is wasteful. The palace remembers so you don't have to re-discover.

## MemPalace

- **Palace data**: `data/` in this repo (gitignored, machine-local)
- **MCP server**: registered globally via `claude mcp add --scope user`
- **Python venv**: `.venv/` in this directory (gitignored)

## MCP Tools

The MemPalace MCP server exposes 19 tools. Key ones:

- `mcp__mempalace__wake_up` — load L0+L1 memory layers (~170 tokens). Call this FIRST in every session.
- `mcp__mempalace__search` — semantic search across all stored knowledge. Use this before Read/Grep for context questions.
- `mcp__mempalace__store` / `remember` — persist new knowledge
- `mcp__mempalace__kg_query` — query the temporal knowledge graph

Run `/mcp` in any Claude Code session to see the full tool list.

## Knowledge CLI

```bash
knowledge setup              # install mempalace, register MCP, configure alias
knowledge index <dir>        # init + mine every subdirectory of <dir>
knowledge version            # print version
```

## MemPalace CLI

After setup, `mempalace` is aliased and available globally:

```bash
mempalace init <project-dir>               # Detect rooms from folder structure
mempalace mine <dir>                        # Ingest project files
mempalace mine <dir> --mode convos          # Ingest conversation exports
mempalace search "query"                    # Search the palace
mempalace wake-up                           # Show L0+L1 context
mempalace wake-up --wing <project>          # Wing-specific context
mempalace status                            # Show what's been filed
mempalace compress                          # Compress using AAAK dialect
```

## Hooks

MemPalace supports Claude Code hooks for automatic memory capture:

```bash
mempalace hook run --hook session-start --harness claude-code
mempalace hook run --hook stop --harness claude-code
mempalace hook run --hook precompact --harness claude-code
```

These read JSON from stdin and output JSON to stdout.

## Setup

Build and run the Go tool:

```bash
make build
./build/knowledge setup
```

Or download a pre-built binary from `build/<os>-<arch>/`.
