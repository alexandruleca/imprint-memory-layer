"""Learn coding-client conversations into imprint memory.

Reads Claude Code session transcripts (plus Claude's auto-memory files), Codex
rollouts, Cursor agent conversations, GitHub Copilot sessions and Cline
sessions, extracts Q+A exchange pairs, classifies each by type, and stores them as searchable memories. A
conversation's recorded working directory decides its workspace (ADR 0034)
and its project name.

A store is the directory a client keeps its conversations in: Claude Code's
CLAUDE_CONFIG_DIR (default ~/.claude), Codex's CODEX_HOME (default ~/.codex),
Cursor's ~/.cursor, Copilot's COPILOT_HOME (default ~/.copilot) or Cline's
sessions directory (default ~/.cline/data/sessions). The Go CLI's client table names them. In a WSL path
context (IMPRINT_WSL_DISTRO, as the WSL bridge sets it) Linux paths, the
store's and the recorded cwds, resolve the way the bridge resolves them
(ADR 0026, ADR 0037).

Usage:
  python -m imprint.cli_conversations --store claude-code|codex|cursor|copilot|cline <dir> [--store ...]
  python -m imprint.cli_conversations --transcript <path>
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imprint import chunker, tagger, transcripts, vectorstore as vs
from imprint.classifier import classify
from imprint._cli_signals import install as _install_signals

_install_signals()

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_YELLOW = "\033[1;33m"

# Minimum useful exchange length — drop sub-100-char noise.
MIN_EXCHANGE_LEN = 100

# A user's home as it begins a folder name: Linux /home/<user>, macOS
# /Users/<user>, Windows <drive>:\Users\<user>.
_HOME_FOLDER = re.compile(r"-home-|-Users-|[A-Za-z]--Users-")

CLIENTS = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "copilot": "GitHub Copilot", "cline": "Cline"}


def parse_exchanges(transcript_path: str) -> list[dict]:
    """Parse a transcript JSONL into Q+A exchange pairs."""
    try:
        entries = list(transcripts.records(transcript_path))
    except (OSError, ValueError):
        return []

    # Get session title if available (Claude Code)
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
        role, text = transcripts.message(e) or (None, "")
        if not text or len(text.strip()) <= 10:
            continue
        if role == "user":
            # If we have a pending pair, save it
            if user_msgs and assistant_msgs:
                exchanges.append(_build_exchange(user_msgs, assistant_msgs, title))
            user_msgs = [text]
            assistant_msgs = []
        else:
            assistant_msgs.append(text)

    # Save last pair
    if user_msgs and assistant_msgs:
        exchanges.append(_build_exchange(user_msgs, assistant_msgs, title))

    return exchanges


def _clean_user_text(text: str) -> str:
    """Strip system tags and noise from user messages."""
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
    """Build a single exchange from user + assistant messages.

    Concats ALL assistant messages between user turns (preserves multi-turn
    flow + tool-call narration). No line cap, no code-heavy skip, no
    truncation — long exchanges are split downstream by chunker.chunk_prose.
    """
    user_text = _clean_user_text("\n".join(user_msgs))

    # Skip if user message is empty after cleaning (was just system noise).
    if len(user_text) < 10:
        return {"text": "", "user_text": "", "title": title}

    # Concat every assistant text block, dropping only ack-tier stubs.
    parts = [m for m in assistant_msgs if len(m) >= 50]
    if not parts:
        return {"text": "", "user_text": "", "title": title}
    assistant_text = "\n\n".join(parts)

    exchange_text = f"Q: {user_text}\n\nA: {assistant_text}"

    return {
        "text": exchange_text,
        "user_text": user_text[:500],
        "title": title,
    }


def derive_project(cwd: str) -> str:
    """Derive the project name from a conversation's recorded working directory.

    The name is read from Claude Code's folder name for that directory (every
    character other than a letter or digit becomes "-"), as learning always
    has: -home-user-code-node-my-web-app gives my-web-app and
    -home-user-code-imprint gives imprint. Using the recorded cwd instead of
    the path a transcript was found at gives a conversation one name however
    its store is reached (natively or through \\\\wsl.localhost), and gives
    Codex, Cursor, Copilot and Cline sessions the same names as Claude Code
    sessions. A home on macOS (/Users/user/code/imprint) or Windows
    (C:\\Users\\user\\code\\imprint) names projects as a Linux home does.
    """
    folder = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    if not _HOME_FOLDER.match(folder) or "code-" not in folder:
        return ""
    remainder = folder[folder.index("code-") + 5:]
    # Skip known group prefixes (org dirs, language dirs, etc.) up to the
    # first segment that starts the project name.
    skip = {"node", "python", "php", "personal", "workspaces"}
    segments = remainder.split("-")
    while segments and segments[0] in skip:
        segments.pop(0)
    return "-".join(segments) or remainder


def identify(transcript_path: str) -> tuple[str, str]:
    """(source key, project) of a transcript, from what it records.

    Claude Code and Cursor name a transcript after its random session id, and
    its first eight characters are the source key. Codex and Cline session
    ids begin with a timestamp, which sessions started close together share,
    and Copilot's can be any string its caller chose, so those are keyed by
    the whole id (transcripts.session).
    """
    session = transcripts.session(transcript_path)
    source = f"conversation/{session or Path(transcript_path).stem[:8]}"
    cwd = next((cwd for cwd in transcripts.cwds(transcript_path) if isinstance(cwd, str)), "")
    return source, derive_project(cwd) if cwd else ""


def store_transcripts(client: str, root: Path) -> list[Path]:
    """Conversation files in a client store."""
    if client == "claude-code":
        # One folder per project; subagent transcripts sit a level deeper.
        return list(root.glob("projects/*/*.jsonl"))
    if client == "cursor":
        # One folder per conversation; its subagents' transcripts sit a level deeper.
        return list(root.glob("projects/*/agent-transcripts/*/*.jsonl"))
    if client == "copilot":
        # One folder per session, named after it.
        return list(root.glob("session-state/*/events.jsonl"))
    if client == "cline":
        # One folder per session, named after it; its subagents' and teammates'
        # messages files there have other names. An imported session belongs
        # to the store it was imported from.
        return [path for path in root.glob("*/*.messages.json")
                if path.name == f"{path.parent.name}.messages.json" and not transcripts.cline_imported(path)]
    return [*root.glob("sessions/**/rollout-*.jsonl"), *root.glob("archived_sessions/rollout-*.jsonl")]


def owned(transcript_path: str) -> bool:
    """Whether a transcript belongs to this workspace (ADR 0034).

    Like the WSL bridge's Stop check, an unreadable transcript, or one whose
    recorded cwd has no Windows representation (ADR 0026), belongs nowhere.
    """
    from .workspace_policy import transcript_allowed
    try:
        return transcript_allowed(transcript_path)
    except (OSError, ValueError):
        return False


def learn_auto_memory(root: Path) -> int:
    """Import a Claude Code profile's auto-memory files. Returns files stored.

    Claude's folder encoding is ambiguous: a folder's memory files are
    imported only when every transcript beside them belongs to this
    workspace (ADR 0034).
    """
    from .workspace_policy import is_excluded
    stored = 0
    for memory in root.glob("projects/*/memory"):
        folder = memory.parent
        sessions = list(folder.glob("*.jsonl"))
        if not sessions or not all(owned(str(path)) for path in sessions):
            continue

        name = folder.name
        project = "general"
        if "personal" in name:
            project = "personal"
        elif "imprint" in name:
            project = "imprint"

        for f in memory.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                continue
            if not content or is_excluded(path=str(f), source=f"memory/{f.name}"):
                continue
            vs.store(content=content, project=project, type="decision", source=f"memory/{f.name}",
                     source_path=str(f.resolve()), source_type="memory")
            stored += 1
    return stored


def index_transcript(transcript_path: str, source_mtime: float = 0.0) -> tuple[int, int]:
    """Index a single transcript. Returns (stored, skipped).

    Buffers all qualifying exchanges and flushes via vs.store_batch() so the
    embedding model sees grouped inputs and Qdrant gets one upsert call per
    transcript instead of one per exchange. Big throughput win because
    `imprint ingest` walks hundreds of transcripts before touching code.
    """
    from .workspace_policy import is_excluded
    source, project = identify(transcript_path)
    if not owned(transcript_path) or is_excluded(path=transcript_path, source=source):
        return 0, 0
    exchanges = parse_exchanges(transcript_path)
    stored = 0
    skipped = 0
    if not source_mtime:
        try:
            source_mtime = os.path.getmtime(transcript_path)
        except OSError:
            pass

    # The LLM classifies these later, in the background (ADR 0067); the
    # regex classifier and keyword tags describe them until then.
    from .tag_backlog import deferring
    pending = deferring()
    records: list[dict] = []
    for ex in exchanges:
        if not ex["text"] or len(ex["text"]) < MIN_EXCHANGE_LEN:
            skipped += 1
            continue

        tags = tagger.build_payload_tags(ex["text"], llm=False)
        tags.pop("_llm_type", None)
        mem_type, confidence = classify(ex["text"])
        if confidence < 0.2:
            mem_type = "finding"
        tags["lang"] = "conversation"
        tags["layer"] = "session"
        tags["kind"] = "qa"

        # Split long exchanges via semantic chunker (with sliding overlap)
        # so retrieval lands on coherent topic segments instead of a single
        # giant embedding or a mid-word truncation.
        pieces = chunker.chunk_prose(ex["text"]) or [ex["text"]]
        for ci, piece in enumerate(pieces):
            records.append({
                "content": piece,
                "project": project,
                "type": mem_type,
                "source": source,
                "source_path": str(Path(transcript_path).resolve()),
                "chunk_index": ci,
                "source_mtime": source_mtime,
                "tags": tags,
                "tag_pending": pending,
            })

    # Flush in small sub-batches so a transcript with hundreds of exchanges
    # doesn't pin a giant batch through tokenization + ONNX at once.
    BATCH = 8
    for i in range(0, len(records), BATCH):
        inserted, _ = vs.store_batch(records[i:i + BATCH])
        stored += inserted

    return stored, skipped


def learn(stores: list[tuple[str, str]]) -> int:
    """Learn every conversation in the given client stores.

    Returns 1 when a store could not be read; the other stores are still
    learned. A conversation reachable through more than one store is learned
    once: its source key is the state, and the newest copy is read.
    """
    from .wsl_paths import native_path

    print()
    found: dict[str, tuple[str, float]] = {}
    unreadable = []
    memory_files = 0
    for client, directory in stores:
        root = Path(native_path(directory))
        label = f"{CLIENTS[client]} {root}"
        try:
            # Globbing skips unreadable folders silently: list the profile
            # itself so a store that cannot be reached is reported.
            os.listdir(root)
        except FileNotFoundError:
            print(f"  {C_DIM}{label}: no profile directory{C_RESET}")
            continue
        except OSError as error:
            unreadable.append(label)
            print(f"  {C_YELLOW}{label}: could not be read ({error}){C_RESET}")
            continue
        paths = store_transcripts(client, root)
        print(f"  {C_CYAN}{label}: {len(paths)} conversations{C_RESET}", flush=True)
        if client == "claude-code":
            memory_files += learn_auto_memory(root)
        for path in paths:
            try:
                mtime = path.stat().st_mtime
                source, _ = identify(str(path))
            except (OSError, ValueError):
                continue
            if source not in found or found[source][1] < mtime:
                found[source] = (str(path), mtime)

    # Pre-filter unchanged transcripts via stored mtime (same logic as cli_index),
    # before reading any conversation in full.
    known_mtimes = vs.get_source_mtimes()
    filtered = []
    pre_skipped = 0
    not_owned = 0
    for source, (path, fmtime) in found.items():
        stored_mtime = known_mtimes.get(source, 0)
        if stored_mtime and abs(stored_mtime - fmtime) < 1.0:
            pre_skipped += 1
            continue
        if not owned(path):
            not_owned += 1
            continue
        filtered.append((path, source, fmtime, bool(stored_mtime)))

    print()
    print(f"  {C_CYAN}Found {len(found)} conversations"
          f" ({pre_skipped} unchanged, {not_owned} not this workspace's, {len(filtered)} to index){C_RESET}")
    print()

    total_stored = 0
    total_skipped = 0
    reindexed = 0
    t_start = time.time()

    try:
        cols = os.get_terminal_size().columns
    except (ValueError, OSError):
        cols = 80

    for idx, (path, source, fmtime, stale) in enumerate(filtered):
        pct = (idx + 1) / len(filtered) if filtered else 1
        elapsed = time.time() - t_start
        eta = elapsed / pct * (1 - pct) if pct < 1 else 0
        stats = f" {int(pct*100):3d}% {idx+1}/{len(filtered)} eta {int(eta)}s"
        bar_width = max(10, cols - len(stats) - 3)
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)
        line = f"  {bar}{stats}"
        print(f"\r{line[:cols]}", end="", flush=True)

        try:
            # Replace the old chunks of a modified transcript.
            if stale:
                vs.delete_by_source(source)
                reindexed += 1
            stored, skipped = index_transcript(path, source_mtime=fmtime)
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
    count = len(filtered) or 1
    stats = f" 100% {count}/{count} {elapsed:.1f}s"
    bar_width = max(10, cols - len(stats) - 3)
    bar = "█" * bar_width
    print(f"\r  {bar}{stats}{' ' * 10}")

    print()
    print(f"  {C_GREEN}═══ Conversations Indexed ═══{C_RESET}")
    print(f"  Stored:   {total_stored} exchanges")
    if memory_files:
        print(f"  Memory:   {memory_files} auto-memory files")
    if pre_skipped:
        print(f"  Unchanged:{pre_skipped} transcripts (skipped)")
    if reindexed:
        print(f"  Re-indexed:{reindexed} modified transcripts")
    print(f"  Skipped:  {total_skipped}")
    print(f"  Time:     {elapsed:.1f}s")
    from .tag_backlog import report
    report(total_stored)
    for label in unreadable:
        print(f"  {C_YELLOW}Not learned: {label} could not be read{C_RESET}")
    print()
    return 1 if unreadable else 0


def main():
    parser = argparse.ArgumentParser(description="Learn coding-client conversations.")
    parser.add_argument("--store", nargs=2, action="append", default=[], metavar=("CLIENT", "DIR"),
                        help="client store to learn (claude-code, codex, cursor, copilot or cline); repeatable")
    parser.add_argument("--transcript", help="index a single transcript")
    args = parser.parse_args()

    if args.transcript:
        stored, skipped = index_transcript(args.transcript)
        print(f"  Stored {stored}, skipped {skipped} exchanges")
        return 0
    if not args.store:
        parser.error("give --store or --transcript")
    for client, _ in args.store:
        if client not in CLIENTS:
            parser.error(f"unknown client {client!r}; expected one of {', '.join(CLIENTS)}")
    return learn([tuple(store) for store in args.store])


if __name__ == "__main__":
    sys.exit(main())
