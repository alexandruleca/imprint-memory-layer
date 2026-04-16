"""Auto-extract decisions, findings, and patterns from Claude Code transcripts.

Called by the Stop hook after each conversation turn.
Reads the transcript JSONL, extracts assistant text messages,
and stores meaningful content as memories.

Usage: python -m imprint.cli_extract <transcript_path> [--project <name>]
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imprint import tagger, vectorstore as vs

# Minimum length for a message to be worth storing
MIN_LENGTH = 200
# Maximum number of messages to extract per session
MAX_MESSAGES = 5
# Skip messages that are mostly code/tool output
CODE_RATIO_THRESHOLD = 0.6

# Signals that a message contains a REAL decision or finding (not casual chat).
# Require at least 2 signal matches per message to reduce false positives.
SIGNAL_PATTERNS = [
    r"\bwe (?:decided|chose|picked|went with)\b",
    r"\bthe (?:decision|choice|approach) (?:is|was)\b",
    r"\bbecause (?:the|this|it|we)\b",
    r"\broot cause\b",
    r"\bbreaking change\b",
    r"\bworkaround\b",
    r"\btrade-?off\b",
    r"\bkey (?:point|finding|takeaway)\b",
    r"\barchitectural(?:ly)?\b",
    r"\bby design\b",
    r"\bintentional(?:ly)?\b",
    r"\bconvention is\b",
    r"\balways use\b.*\binstead of\b",
    r"\bnever use\b",
    r"\bdeprecated in favor of\b",
    r"\bupgraded? from\b.*\bto\b",
    r"\bmigrated? from\b.*\bto\b",
]

SIGNAL_RES = [re.compile(p, re.IGNORECASE) for p in SIGNAL_PATTERNS]

# Skip messages that look like meta-conversation about the tool itself
SKIP_PATTERNS = [
    r"here'?s what (?:changed|was done|happened)",
    r"(?:all |everything )?done\.?\s*(?:here|let me)",
    r"let me (?:test|verify|check|build|rebuild)",
    r"^(?:good|great|nice|perfect|done)\b",
    r"want me to",
    r"(?:commit|push) (?:and|this)",
    r"honest take",
]
SKIP_RE = re.compile("|".join(SKIP_PATTERNS), re.IGNORECASE)


def extract_text_from_message(msg: dict) -> str:
    """Extract plain text content from a Claude message, skipping tool calls and thinking."""
    content = msg.get("content", [])
    if isinstance(content, str):
        return content

    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(block["text"])
    return "\n\n".join(texts)


def code_ratio(text: str) -> float:
    """Estimate what fraction of text is code."""
    lines = text.split("\n")
    if not lines:
        return 0
    code_lines = 0
    in_block = False
    for line in lines:
        if line.strip().startswith("```"):
            in_block = not in_block
            code_lines += 1
        elif in_block:
            code_lines += 1
        elif line.startswith("    ") or line.startswith("\t"):
            code_lines += 1
    return code_lines / len(lines)


def count_signals(text: str) -> int:
    """Count how many signal patterns match. Need >= 2 for a real decision."""
    return sum(1 for r in SIGNAL_RES if r.search(text))


def is_meta(text: str) -> bool:
    """Check if message is meta-conversation (about the tool/process, not the project)."""
    first_line = text.split("\n")[0][:200]
    return bool(SKIP_RE.search(first_line))


def classify_type(text: str) -> str:
    """Classify memory type using the shared classifier."""
    from imprint.classifier import classify
    mem_type, _ = classify(text)
    return mem_type


def derive_project_from_path(transcript_path: str) -> str:
    """Try to derive project name from the transcript path."""
    parts = transcript_path.split("/")
    for part in parts:
        if part.startswith("-home-") and "code-" in part:
            idx = part.index("code-") + 5
            remainder = part[idx:]
            if remainder.startswith("personal"):
                return "personal"
            return remainder.split("-")[0]
    return ""


def main():
    transcript_path = None
    project = ""

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]
            i += 2
        elif not transcript_path:
            transcript_path = args[i]
            i += 1
        else:
            i += 1

    if not transcript_path or not os.path.exists(transcript_path):
        sys.exit(0)

    if not project:
        project = derive_project_from_path(transcript_path)

    # Read transcript
    messages = []
    try:
        with open(transcript_path, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "assistant":
                    msg = entry.get("message", {})
                    text = extract_text_from_message(msg)
                    if text:
                        messages.append(text)
    except Exception:
        sys.exit(0)

    if not messages:
        sys.exit(0)

    # Filter and score messages
    candidates = []
    for text in messages:
        if len(text) < MIN_LENGTH:
            continue
        if code_ratio(text) > CODE_RATIO_THRESHOLD:
            continue
        if is_meta(text):
            continue
        signals = count_signals(text)
        if signals < 2:
            continue
        # Truncate long messages
        if len(text) > 2000:
            text = text[:2000]
        candidates.append((signals, text))

    if not candidates:
        sys.exit(0)

    # Sort by signal count (most signals = strongest decisions), take top N
    candidates.sort(key=lambda x: -x[0])
    stored = 0
    for _, text in candidates[:MAX_MESSAGES]:
        tags = tagger.build_payload_tags(text)
        llm_type = tags.pop("_llm_type", "")
        mem_type = llm_type or classify_type(text)
        tags["lang"] = "conversation"
        tags["layer"] = "session"
        tags["kind"] = "auto-extract"
        vs.store(
            content=text,
            project=project,
            type=mem_type,
            source="auto-extract",
            tags=tags,
        )
        stored += 1

    if stored > 0:
        print(f"  Auto-extracted {stored} memories from session", file=sys.stderr)


if __name__ == "__main__":
    main()
