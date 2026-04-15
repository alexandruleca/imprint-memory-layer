# MCP Tools & Automatic Updates

## MCP Tools

Claude Code gets 8 tools via the imprint MCP server:

| Tool | Purpose |
|------|---------|
| `wake_up` | Load prior context at session start (~800 tokens) |
| `search` | Semantic search with `project`/`type`/`lang`/`layer`/`kind`/`domain` filters |
| `store` | Save a memory — auto-classified as decision/pattern/bug/etc. |
| `delete` | Remove a memory by ID |
| `kg_add` | Add a temporal fact (subject → predicate → object) |
| `kg_query` | Query facts with optional time filtering |
| `kg_invalidate` | Mark a fact as no longer valid |
| `status` | Show memory count by project |

Search filters map to payload tags — see [tagging.md](./tagging.md) for the full tag schema.

## Automatic Updates

The imprint memory stays current through three mechanisms:

- **Stop hook** (async) — after each Claude response, parses the conversation transcript, extracts Q+A exchanges and decision-like statements, stores them automatically with `lang=conversation` tags
- **PreCompact hook** (sync) — before Claude's context window gets compressed, blocks and instructs Claude to save all important context via MCP tools
- **`imprint refresh`** — compares file modification times via `vs.get_source_mtimes()`, only re-chunks + re-embeds what changed

Both hooks are installed by `imprint setup` into `~/.claude/settings.json` and removed by `imprint disable`.
