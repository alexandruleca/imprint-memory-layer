#!/bin/sh
# Imprint's Claude Code notice hook (ADRs 0212, 0240): print, once, what this
# conversation's Imprint server prepared for it, after a tool call
# (PostToolUse) or before a prompt (UserPromptSubmit), without starting
# Python. Claude Code sources it in the hook's own shell
# (IMPRINT_PROMPT_DATA=DATA_DIR IMPRINT_NOTICE_EVENT=EVENT . prompt_notices.sh);
# the WSL bridge runs it (sh prompt_notices.sh DATA_DIR EVENT). The event
# defaults to UserPromptSubmit, as the hook of earlier releases registered
# it. session_id comes first in Claude Code's input, so only its start is
# read; in bash (Git Bash on Windows, where each process costs tens of
# milliseconds) nothing else starts unless a notice waits.
data=${IMPRINT_PROMPT_DATA:-$1}
event=${IMPRINT_NOTICE_EVENT:-${2:-UserPromptSubmit}}
case "$event" in UserPromptSubmit | PostToolUse) ;; *) exit 0 ;; esac
if [ -n "${BASH_VERSION:-}" ]; then
	IFS= read -r -n 1024 input
else
	input=$(head -c 1024)
fi
id=${input#*\"session_id\"}
[ "$id" != "$input" ] || exit 0
id=${id#*\"}
id=${id%%\"*}
case "$id" in '' | *[!A-Za-z0-9-]*) exit 0 ;; esac
file="$data/prompt-notices/$id.notice"
[ -f "$file" ] || exit 0
# Which hook told it, for the server to record; written before the file is
# taken, so the server never finds the notice gone without it.
printf '%s' "$event" >"$data/prompt-notices/$id.notice-by" 2>/dev/null
taken="$file.$$.taken"
mv "$file" "$taken" 2>/dev/null || exit 0
printf '{"hookSpecificOutput":{"hookEventName":"%s","additionalContext":' "$event"
cat "$taken"
printf '}}\n'
rm -f "$taken"
exit 0
