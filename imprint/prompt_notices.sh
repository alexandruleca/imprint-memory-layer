#!/bin/sh
# Imprint's Claude Code prompt hook (ADR 0212): print, once, what this
# conversation's Imprint server prepared for its next prompt, without
# starting Python. Claude Code sources it in the hook's own shell
# (IMPRINT_PROMPT_DATA=DATA_DIR . prompt_notices.sh); the WSL bridge runs it
# (sh prompt_notices.sh DATA_DIR). session_id comes first in Claude Code's
# input, so only its start is read; in bash (Git Bash on Windows, where each
# process costs tens of milliseconds) nothing else starts unless a notice
# waits.
data=${IMPRINT_PROMPT_DATA:-$1}
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
file="$data/prompt-notices/$id.json"
[ -f "$file" ] || exit 0
taken="$file.$$.taken"
mv "$file" "$taken" 2>/dev/null || exit 0
cat "$taken"
rm -f "$taken"
exit 0
