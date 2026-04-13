"""Index Claude Code conversation transcripts into the knowledge base.

Parses JSONL transcripts, extracts Q+A exchange pairs, classifies each
by type, and stores as searchable memories.

Usage: python -m knowledgebase.cli_conversations [--all] [--transcript <path>]
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledgebase import vectorstore as vs
from knowledgebase.classifier import classify

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_YELLOW = "\033[1;33m"

# Minimum useful exchange length
MIN_EXCHANGE_LEN = 100
# Soft max — preferred size, can overflow to keep complete thoughts
TARGET_EXCHANGE_LEN = 4000
# Hard max — absolute limit (model context is 8192 tokens ~ 24000 chars)
HARD_MAX_EXCHANGE_LEN = 8000
# Skip assistant messages that are mostly tool calls / code
CODE_LINE_THRESHOLD = 0.7
# Cap assistant response at N lines to avoid storing huge dumps
MAX_ASSISTANT_LINES = 60


def extract_text(msg: dict) -> str:
    """Extract plain text from a Claude message, skipping thinking/tool_use blocks."""
    content = msg.get("content", [])
    if isinstance(content, str):
        return content
    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(block["text"])
    return "\n\n".join(texts)


def parse_exchanges(transcript_path: str) -> list[dict]:
    """Parse a transcript JSONL into Q+A exchange pairs."""
    entries = []
    try:
        with open(transcript_path, "r") as f:
            for line in f:
                try:
                    entries.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []

    # Get session title if available
    title = ""
    for e in entries:
        if e.get("type") == "ai-title":
            title = e.get("aiTitle", "")
            break

    # Pair user messages with following assistant responses
    exchanges = []
    user_msgs = []
    assistant_msgs = []

    for e in entries:
        if e.get("type") == "user":
            text = extract_text(e.get("message", {}))
            if text and len(text.strip()) > 10:
                # If we have a pending pair, save it
                if user_msgs and assistant_msgs:
                    exchanges.append(_build_exchange(user_msgs, assistant_msgs, title))
                user_msgs = [text]
                assistant_msgs = []
        elif e.get("type") == "assistant":
            text = extract_text(e.get("message", {}))
            if text and len(text.strip()) > 10:
                assistant_msgs.append(text)

    # Save last pair
    if user_msgs and assistant_msgs:
        exchanges.append(_build_exchange(user_msgs, assistant_msgs, title))

    return exchanges


def _clean_user_text(text: str) -> str:
    """Strip system tags and noise from user messages."""
    import re
    # Remove XML-style system tags
    text = re.sub(r"<[^>]+>.*?</[^>]+>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+/>", "", text)
    # Remove system-reminder blocks
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL)
    # Remove task-notification blocks
    text = re.sub(r"<task-notification>.*?</task-notification>", "", text, flags=re.DOTALL)
    # Remove ide_opened_file / ide_selection tags
    text = re.sub(r"<ide_\w+>.*?</ide_\w+>", "", text, flags=re.DOTALL)
    return text.strip()


def _build_exchange(user_msgs: list[str], assistant_msgs: list[str], title: str) -> dict:
    """Build a single exchange from user + assistant messages."""
    user_text = _clean_user_text("\n".join(user_msgs))

    # Skip if user message is empty after cleaning (was just system noise)
    if len(user_text) < 10:
        return {"text": "", "user_text": "", "title": title, "code_heavy": False}

    # Take the most substantial assistant response (not just "Let me look...")
    best_assistant = ""
    for msg in assistant_msgs:
        # Skip very short responses (tool call acknowledgements)
        if len(msg) < 50:
            continue
        if len(msg) > len(best_assistant):
            best_assistant = msg

    if not best_assistant:
        return {"text": "", "user_text": "", "title": title, "code_heavy": False}

    # Cap assistant response by lines
    lines = best_assistant.split("\n")
    if len(lines) > MAX_ASSISTANT_LINES:
        best_assistant = "\n".join(lines[:MAX_ASSISTANT_LINES])

    # Check code ratio — skip if mostly code
    code_lines = sum(1 for l in lines if l.strip().startswith(("```", "  ", "\t", "import ", "from ", "const ", "export ")))
    code_ratio = code_lines / max(len(lines), 1)

    exchange_text = f"Q: {user_text}\n\nA: {best_assistant}"

    # Smart truncation — keep complete sentences/paragraphs
    if len(exchange_text) > HARD_MAX_EXCHANGE_LEN:
        exchange_text = _smart_truncate(exchange_text, HARD_MAX_EXCHANGE_LEN)

    return {
        "text": exchange_text,
        "user_text": user_text[:500],
        "title": title,
        "code_heavy": code_ratio > CODE_LINE_THRESHOLD,
    }


def _smart_truncate(text: str, max_len: int) -> str:
    """Truncate at a natural boundary — paragraph, sentence, or line break."""
    if len(text) <= max_len:
        return text

    cut = text[:max_len]

    # Try paragraph break
    pos = cut.rfind("\n\n")
    if pos > max_len * 0.6:
        return cut[:pos]

    # Try sentence end
    for end in [". ", ".\n", "? ", "?\n", "! ", "!\n"]:
        pos = cut.rfind(end)
        if pos > max_len * 0.6:
            return cut[:pos + 1]

    # Try line break
    pos = cut.rfind("\n")
    if pos > max_len * 0.5:
        return cut[:pos]

    return cut


def derive_project(transcript_path: str) -> str:
    """Derive project name from transcript path.

    Claude project dirs look like: -home-hunter-code-brightspaces-node-auto-space-api
    We want the last meaningful segment: auto-space-api
    For paths like -home-hunter-code-knowledge, we get: knowledge
    """
    parts = transcript_path.split("/")
    for part in parts:
        if part.startswith("-home-") and "code-" in part:
            idx = part.index("code-") + 5
            remainder = part[idx:]
            # Split by known grouping prefixes and take the last project name
            segments = remainder.split("-")
            # Skip known group prefixes: brightspaces, node, python, php, personal
            skip = {"brightspaces", "node", "python", "php", "personal", "workspaces"}
            # Find the first non-skip segment — that starts the project name
            project_parts = []
            found_start = False
            for seg in segments:
                if not found_start and seg in skip:
                    continue
                found_start = True
                project_parts.append(seg)
            if project_parts:
                return "-".join(project_parts)
            return remainder
    return ""


def find_all_transcripts() -> list[tuple[str, str]]:
    """Find all Claude Code transcripts. Returns [(path, project)]."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return []

    results = []
    for project_dir in claude_dir.iterdir():
        if not project_dir.is_dir():
            continue
        project = derive_project(str(project_dir))
        for f in project_dir.glob("*.jsonl"):
            if "subagents" not in str(f):
                results.append((str(f), project))

    # Sort by mtime (newest first)
    results.sort(key=lambda x: os.path.getmtime(x[0]), reverse=True)
    return results


def index_transcript(transcript_path: str, project: str) -> tuple[int, int]:
    """Index a single transcript. Returns (stored, skipped).

    Buffers all qualifying exchanges and flushes via vs.store_batch() so the
    embedding model sees grouped inputs and LanceDB sees one fragment per
    transcript instead of one per exchange. Big OOM/throughput win because
    `knowledge ingest` walks hundreds of transcripts before touching code.
    """
    exchanges = parse_exchanges(transcript_path)
    stored = 0
    skipped = 0
    session_id = Path(transcript_path).stem[:8]

    records: list[dict] = []
    for ex in exchanges:
        if not ex["text"] or len(ex["text"]) < MIN_EXCHANGE_LEN:
            skipped += 1
            continue
        if ex["code_heavy"]:
            skipped += 1
            continue

        mem_type, confidence = classify(ex["text"])
        if confidence < 0.2:
            mem_type = "finding"

        records.append({
            "content": ex["text"],
            "project": project,
            "type": mem_type,
            "source": f"conversation/{session_id}",
            "tags": "conversation",
        })

    # Flush in small sub-batches so a transcript with hundreds of exchanges
    # doesn't pin a giant batch through tokenization + ONNX at once.
    BATCH = 8
    for i in range(0, len(records), BATCH):
        inserted, _ = vs.store_batch(records[i:i + BATCH])
        stored += inserted

    return stored, skipped


def main():
    args = sys.argv[1:]
    transcript_path = None
    index_all = False

    i = 0
    while i < len(args):
        if args[i] == "--all":
            index_all = True
            i += 1
        elif args[i] == "--transcript" and i + 1 < len(args):
            transcript_path = args[i + 1]
            i += 2
        else:
            i += 1

    if transcript_path:
        # Single transcript
        project = derive_project(transcript_path)
        stored, skipped = index_transcript(transcript_path, project)
        print(f"  Stored {stored}, skipped {skipped} exchanges")
        return

    if not index_all:
        print("Usage:")
        print("  python -m knowledgebase.cli_conversations --all")
        print("  python -m knowledgebase.cli_conversations --transcript <path>")
        sys.exit(1)

    # Index all transcripts
    transcripts = find_all_transcripts()
    if not transcripts:
        print("  No transcripts found.")
        return

    print()
    print(f"  {C_CYAN}Found {len(transcripts)} transcripts{C_RESET}")
    print()

    total_stored = 0
    total_skipped = 0
    t_start = time.time()

    try:
        cols = os.get_terminal_size().columns
    except (ValueError, OSError):
        cols = 80

    for idx, (path, project) in enumerate(transcripts):
        pct = (idx + 1) / len(transcripts)
        elapsed = time.time() - t_start
        eta = elapsed / pct * (1 - pct) if pct < 1 else 0
        stats = f" {int(pct*100):3d}% {idx+1}/{len(transcripts)} eta {int(eta)}s"
        bar_width = max(10, cols - len(stats) - 3)
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)
        line = f"  {bar}{stats}"
        print(f"\r{line[:cols]}", end="", flush=True)

        try:
            stored, skipped = index_transcript(path, project)
            total_stored += stored
            total_skipped += skipped
        except KeyboardInterrupt:
            print()
            print()
            print(f"  {C_YELLOW}Cancelled{C_RESET} — progress saved. Re-run to continue.")
            break
        except Exception:
            continue

    # Final bar
    elapsed = time.time() - t_start
    stats = f" 100% {len(transcripts)}/{len(transcripts)} {elapsed:.1f}s"
    bar_width = max(10, cols - len(stats) - 3)
    bar = "█" * bar_width
    print(f"\r  {bar}{stats}{' ' * 10}")

    print()
    print(f"  {C_GREEN}═══ Conversations Indexed ═══{C_RESET}")
    print(f"  Stored:   {total_stored} exchanges")
    print(f"  Skipped:  {total_skipped}")
    print(f"  Time:     {elapsed:.1f}s")
    print()


if __name__ == "__main__":
    main()
