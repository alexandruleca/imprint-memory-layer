"""Local-LLM session summarizer.

Reads a Claude Code transcript JSONL, extracts decisions/patterns/bugs via
a single LLM call, and stores them as memories plus one `type:summary`
record. Runs on Stop hook only when ``summarizer.enabled`` is true.

Defaults to a LOCAL provider (ollama) so the feature is zero-API-cost.
Remote providers (anthropic/openai/gemini) are opt-in and will warn when
invoked.

Usage:
    python -m imprint.cli_summarize <transcript_path> [--project <name>]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imprint import config_schema
from imprint import vectorstore as vs

_SUMMARIZER_PROMPT = (
    "Summarize this Claude Code session transcript. Extract structured entries.\n\n"
    "Return ONLY a JSON object with these keys (each a short list, can be empty):\n"
    '  "decisions": [{ "content": str, "why": str }, ...]   // explicit choices the user/assistant made\n'
    '  "bugs":      [{ "content": str, "why": str }, ...]   // root causes of problems encountered\n'
    '  "patterns":  [{ "content": str, "why": str }, ...]   // reusable techniques surfaced\n'
    '  "facts":     [{ "subject": str, "predicate": str, "object": str }, ...]  // durable facts (subj predicate obj)\n'
    '  "summary":   str                                      // 2-4 sentence recap of the session\n'
    "\n"
    "Rules:\n"
    "- Only include items that will be useful in a FUTURE unrelated session.\n"
    "- Skip per-session trivia, tool outputs, and step-by-step narration.\n"
    "- No markdown, no code fences, no surrounding text — JSON object only.\n\n"
    "Transcript:\n"
)


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


# Codex CLI (via #[serde(tag="type")]) emits these EventMsg variants per line
# into its rollout JSONL. We only keep the ones that carry user/assistant text.
_CODEX_USER_TYPES = {"user_message"}
_CODEX_ASSISTANT_TYPES = {"agent_message", "agent_reasoning"}


def _detect_format(first_line: str) -> str:
    """Inspect the first JSONL record and guess the transcript format.

    Returns 'claude', 'codex', or 'unknown'. The extractor below handles both
    known formats explicitly; 'unknown' falls through to best-effort parsing.
    """
    try:
        obj = json.loads(first_line)
    except (json.JSONDecodeError, ValueError):
        return "unknown"
    t = obj.get("type")
    # Claude Code: {"type": "user"|"assistant", "message": {"content": [...]}}
    if t in ("user", "assistant") and isinstance(obj.get("message"), dict):
        return "claude"
    # Codex rollout: {"type": "<snake_case_event>", "message": "...string..."} etc.
    # Matches both record events (thread.started, turn_started) and content events
    # (agent_message, user_message, agent_reasoning).
    if isinstance(t, str) and ("_" in t or t in _CODEX_USER_TYPES | _CODEX_ASSISTANT_TYPES):
        return "codex"
    if "role" in obj and "content" in obj:
        return "codex"
    return "unknown"


def _extract_transcript_text(path: Path, max_tokens: int) -> str:
    """Flatten transcript JSONL into role-tagged prose. Newest-first truncation.

    Tolerates multiple agent formats:
      * Claude Code: {"type": "user|assistant", "message": {"content": [...]}}
      * Codex CLI rollout: {"type": "agent_message|user_message|...", "message": "str"}
        with events for reasoning, exec_command_begin/end, turn lifecycle, etc.
      * Generic OpenAI chat: {"role": "user|assistant", "content": "..." | [...]}
    """
    entries: list[str] = []
    if not path.exists():
        return ""
    with path.open() as fp:
        first_line = fp.readline()
        fmt = _detect_format(first_line)
        fp.seek(0)
        if fmt != "claude":
            print(f"imprint cli_summarize: transcript format detected as '{fmt}'", file=sys.stderr)
        for line in fp:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if fmt == "codex":
                # Codex rollout: top-level `type` is an event tag. Only the
                # three content-carrying variants produce transcript text.
                t = rec.get("type") or ""
                msg = rec.get("message")
                if t in _CODEX_USER_TYPES and isinstance(msg, str):
                    text = msg.strip()
                    if text:
                        entries.append(f"USER:\n{text}")
                    continue
                if t in _CODEX_ASSISTANT_TYPES:
                    # agent_message -> message: String; agent_reasoning -> text: String
                    text = msg if isinstance(msg, str) else rec.get("text", "")
                    text = (text or "").strip()
                    if text:
                        entries.append(f"ASSISTANT:\n{text}")
                continue

            # Claude Code + generic fallthrough path.
            role = rec.get("type") or rec.get("role")
            msg = rec.get("message") or rec
            if isinstance(msg, str):
                text = msg
            else:
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = []
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "text":
                            parts.append(b.get("text", ""))
                        elif isinstance(b, str):
                            parts.append(b)
                    text = "\n".join(parts)
                else:
                    continue
            text = text.strip()
            if not text:
                continue
            prefix = "USER:" if role in ("user", "human") else "ASSISTANT:"
            entries.append(f"{prefix}\n{text}")

    # Drop oldest entries until under budget
    joined = "\n\n".join(entries)
    if _estimate_tokens(joined) <= max_tokens:
        return joined
    kept: list[str] = []
    tok = 0
    for e in reversed(entries):
        etok = _estimate_tokens(e)
        if tok + etok > max_tokens:
            break
        kept.append(e)
        tok += etok
    kept.reverse()
    return "\n\n".join(kept)


def _call_summarizer_llm(full_input: str) -> str:
    """Call the configured summarizer provider. Returns raw text (expected JSON)."""
    provider = config_schema.resolve("summarizer.provider")[0] or "ollama"
    model = config_schema.resolve("summarizer.model")[0] or "qwen3:1.7b"
    base_url = config_schema.resolve("summarizer.base_url")[0]

    is_remote = provider in ("anthropic", "openai", "gemini")
    if is_remote:
        print(
            f"imprint summarizer: using REMOTE provider '{provider}' — this spends API tokens.",
            file=sys.stderr,
        )

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return ""
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": full_input}],
        )
        return resp.content[0].text if resp.content else ""

    # OpenAI-compatible path (ollama, vllm, openai, gemini)
    key_env_map = {"openai": "OPENAI_API_KEY", "gemini": "GOOGLE_API_KEY"}
    api_key = os.environ.get(key_env_map.get(provider, ""), "no-key-needed")

    default_urls = {
        "ollama": "http://localhost:11434/v1",
        "vllm":   "http://localhost:8000/v1",
        "openai": None,
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    }
    url = base_url or default_urls.get(provider)

    import openai
    kwargs: dict = {"api_key": api_key}
    if url:
        kwargs["base_url"] = url
    client = openai.OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": full_input}],
    )
    return resp.choices[0].message.content if resp.choices else ""


def _parse_json_lenient(text: str) -> dict:
    """Strip fences / leading text; return parsed dict or {}."""
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.endswith("```"):
            s = s[: s.rfind("```")]
    # Drop anything before first {
    i = s.find("{")
    j = s.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return {}
    try:
        return json.loads(s[i : j + 1])
    except json.JSONDecodeError:
        return {}


def summarize_transcript(
    transcript_path: str | Path,
    project: str = "",
) -> dict:
    """Run the summarizer. Stores memories. Returns parse result + counts."""
    if not config_schema.resolve("summarizer.enabled")[0]:
        return {"status": "disabled"}

    path = Path(transcript_path)
    min_msgs = int(config_schema.resolve("summarizer.min_messages")[0] or 5)
    max_in_tok = int(config_schema.resolve("summarizer.max_input_tokens")[0] or 20000)

    body = _extract_transcript_text(path, max_in_tok)
    if not body or body.count("\nUSER:") + body.count("USER:\n") < min_msgs:
        return {"status": "skipped", "reason": "too-short"}

    raw = _call_summarizer_llm(_SUMMARIZER_PROMPT + body)
    parsed = _parse_json_lenient(raw)
    if not parsed:
        return {"status": "failed", "reason": "no-json", "raw_preview": raw[:200]}

    session_id = path.stem or "session"
    source = f"session/{session_id}"

    counts = {"decisions": 0, "bugs": 0, "patterns": 0, "facts": 0}
    for key, mem_type in [
        ("decisions", "decision"),
        ("bugs", "bug"),
        ("patterns", "pattern"),
    ]:
        for item in parsed.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            content = item.get("content", "").strip()
            if not content:
                continue
            why = item.get("why", "").strip()
            full = f"{content}\n\nWhy: {why}" if why else content
            vs.store(
                content=full,
                project=project,
                type=mem_type,
                source=source,
                tags={},
            )
            counts[key] += 1

    # Store the raw summary blurb so future `search` gets a dense hit
    summary = (parsed.get("summary") or "").strip()
    if summary:
        vs.store(
            content=summary,
            project=project,
            type="summary",
            source=source,
            tags={},
        )

    # Facts → kg
    from . import imprint_graph as kg
    for f in parsed.get("facts", []) or []:
        if not isinstance(f, dict):
            continue
        subj = f.get("subject", "").strip()
        pred = f.get("predicate", "").strip()
        obj = f.get("object", "").strip()
        if subj and pred and obj:
            kg.add(subject=subj, predicate=pred, object=obj, source=source)
            counts["facts"] += 1

    return {"status": "ok", "counts": counts}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", help="Path to transcript .jsonl")
    ap.add_argument("--project", default="", help="Project label for stored memories")
    args = ap.parse_args()

    result = summarize_transcript(args.transcript, project=args.project)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
