// Package hooks builds the bash+python hook command strings Imprint
// registers with supported agents (Claude Code, Codex CLI, Cursor). Each
// builder returns the exact command string to hand to the agent's hook
// registration helper.
//
// The inline Python snippets read `session_id` and `transcript_path` from
// hook stdin — these keys are present in both Claude Code and Codex hook
// payloads. Cursor's stop/sessionEnd payload omits `transcript_path`, so
// StopCommand's transcript-dependent work becomes a no-op on Cursor (the
// snippet guards with `if tp:`).
package hooks

import (
	"fmt"
	"path/filepath"
)

// Paths bundles the backend paths the hook commands reference. Mirrors the
// private cmd.backendPaths struct; duplicated here to avoid exporting the
// cmd type and creating an import cycle.
type Paths struct {
	ProjectDir string
	VenvPython string
	DataDir    string
}

// StopCommand runs the transcript-ingest + decision-extract + optional
// summarize pipeline. The Python block no-ops when no `transcript_path` is
// supplied (e.g., on a future Cursor release that fires `stop` without a
// transcript).
func StopCommand(bp Paths) string {
	return fmt.Sprintf(
		`PYTHONPATH=%s IMPRINT_DATA_DIR=%s %s -c "
import json,sys,subprocess,os
d=json.loads(sys.stdin.read())
tp=d.get('transcript_path','')
if tp:
    subprocess.run([sys.executable,'-m','imprint.cli_conversations','--transcript',tp],env=os.environ)
    subprocess.run([sys.executable,'-m','imprint.cli_extract',tp],env=os.environ)
    # Summarizer is gated on summarizer.enabled; the module returns {'status':'disabled'} when off.
    subprocess.run([sys.executable,'-m','imprint.cli_summarize',tp],env=os.environ)
" 2>/dev/null`,
		bp.ProjectDir, bp.DataDir, bp.VenvPython,
	)
}

// PreCompactCommand tells Claude Code to flush important context before
// compression. Claude Code's PreCompact hook accepts {decision:"block",
// reason:"..."} to pause compaction and prompt the agent to save context
// first.
func PreCompactCommand() string {
	return `echo '{"decision":"block","reason":"COMPACTION IMMINENT. Save ALL topics, decisions, and important context from this session using the imprint MCP tools (store, kg_edit). Be thorough — after compaction, detailed context will be lost."}'`
}

// CursorPreCompactCommand is the Cursor variant of PreCompactCommand.
// Cursor's preCompact hook is observation-only — it cannot block — but it
// accepts a `user_message` field that is shown to the user when compaction
// fires. Use that as a nudge to save context manually.
func CursorPreCompactCommand() string {
	return `echo '{"user_message":"Imprint MCP: compaction imminent. Save important decisions, patterns, and findings to memory (call mcp__imprint__store or mcp__imprint__kg_edit) before detailed context is lost."}'`
}

// SessionStartCommand injects the search-first reminder so the agent sees
// the contract in fresh context (no rules-file drift after compaction).
func SessionStartCommand() string {
	return `echo '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Imprint MCP available. Call mcp__imprint__search BEFORE Read/Grep when answering context questions — Read/Grep will be blocked until you do (search auto-loads session context)."}}'`
}

// PostSearchSentinelCommand writes a per-session sentinel file after
// `mcp__imprint__search` runs. Paired with PreReadGateCommand so Read/Grep
// flow normally once the agent has consulted memory.
func PostSearchSentinelCommand(bp Paths) string {
	sentinelDir := filepath.Join(bp.DataDir, ".sessions")
	return fmt.Sprintf(
		`%s -c "
import json,sys,os,pathlib
try:
    d=json.loads(sys.stdin.read())
    sid=d.get('session_id','default')
    p=pathlib.Path(r'%s'); p.mkdir(parents=True,exist_ok=True)
    (p/sid).touch()
except Exception:
    pass
" 2>/dev/null`,
		bp.VenvPython, sentinelDir,
	)
}

// PreReadGateCommand blocks Read/Grep/Glob until PostSearchSentinelCommand
// has fired for this session. Exits 2 with a stderr message to force the
// agent to call search first. Exits 0 on any parse error so a malformed
// hook payload never wedges the agent.
func PreReadGateCommand(bp Paths) string {
	sentinelDir := filepath.Join(bp.DataDir, ".sessions")
	return fmt.Sprintf(
		`%s -c "
import json,sys,os,pathlib
try:
    d=json.loads(sys.stdin.read())
    sid=d.get('session_id','default')
    p=pathlib.Path(r'%s')/sid
    if p.exists():
        sys.exit(0)
    sys.stderr.write('Imprint MCP gate: call mcp__imprint__search before Read/Grep/Glob. The knowledge base may already have the answer.\n')
    sys.exit(2)
except SystemExit:
    raise
except Exception:
    sys.exit(0)
"`,
		bp.VenvPython, sentinelDir,
	)
}
