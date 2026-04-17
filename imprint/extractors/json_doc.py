"""JSON extractor with structured splitting.

Detection cascade:
  1. Known formats (ChatGPT, Anthropic) → per-conversation docs
  2. Generic conversation arrays → auto-detected role+text → per-item docs
  3. Generic object arrays → per-item docs with smart text extraction
  4. Large single objects → split by dominant array field
  5. Fallback → single pretty-printed doc

Large arrays are always split into individual docs so the chunker and
embedder don't choke on a single massive document.
"""

from __future__ import annotations

import json as _json
import os
from datetime import datetime, timezone
from typing import Any

from . import ExtractedDoc, ExtractorResult, ExtractionError, register_ext


# ── Helpers ──────────────────────────────────────────────────

_ROLE_LABEL = {
    "system": "System",
    "user": "User",
    "assistant": "Assistant",
    "tool": "Tool",
    "function": "Tool",
}


def _ts(t: Any) -> str:
    try:
        if t is None:
            return ""
        return datetime.fromtimestamp(float(t), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def _flatten_parts(parts: Any) -> str:
    """ChatGPT message.content.parts may be strings, dicts (image/audio refs),
    or nested lists. Pull out the text only."""
    if parts is None:
        return ""
    if isinstance(parts, str):
        return parts
    if isinstance(parts, list):
        out: list[str] = []
        for p in parts:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict):
                # Common shapes: {"text": "..."}, {"content_type": "image_asset_pointer", ...}
                if "text" in p and isinstance(p["text"], str):
                    out.append(p["text"])
                elif p.get("content_type", "").startswith("image"):
                    out.append("[image]")
                elif p.get("content_type", "").startswith("audio"):
                    out.append("[audio]")
        return "\n".join(out)
    return str(parts)


def _walk_mapping(mapping: dict) -> list[dict]:
    """Walk ChatGPT's message tree from the root, depth-first along the
    first child branch (the canonical conversation thread). Returns
    ordered list of {role, text, ts} dicts."""
    if not isinstance(mapping, dict) or not mapping:
        return []

    # Find root: node whose parent is None / missing
    root_id = None
    for nid, node in mapping.items():
        if not isinstance(node, dict):
            continue
        if not node.get("parent"):
            root_id = nid
            break
    if root_id is None:
        root_id = next(iter(mapping))

    out: list[dict] = []
    cur = root_id
    seen: set[str] = set()
    while cur and cur in mapping and cur not in seen:
        seen.add(cur)
        node = mapping[cur]
        msg = node.get("message") if isinstance(node, dict) else None
        if isinstance(msg, dict):
            author = msg.get("author") or {}
            role = (author.get("role") or "").lower()
            content = msg.get("content") or {}
            text = ""
            if isinstance(content, dict):
                ctype = content.get("content_type", "")
                if ctype in ("text", ""):
                    text = _flatten_parts(content.get("parts"))
                elif ctype == "code":
                    text = "```\n" + (content.get("text") or "") + "\n```"
                elif ctype == "multimodal_text":
                    text = _flatten_parts(content.get("parts"))
                else:
                    # Best-effort: dump remaining content
                    text = _flatten_parts(content.get("parts")) or ""
            text = (text or "").strip()
            if role and text and role != "system":
                out.append({
                    "role": role,
                    "text": text,
                    "ts": msg.get("create_time"),
                })
        children = node.get("children") or []
        cur = children[0] if children else None
    return out


def _conversation_to_md(conv: dict) -> str | None:
    """Render a single ChatGPT conversation dict as Markdown."""
    title = (conv.get("title") or "Untitled").strip()
    created = _ts(conv.get("create_time"))
    updated = _ts(conv.get("update_time"))

    turns: list[dict] = []
    if isinstance(conv.get("mapping"), dict):
        turns = _walk_mapping(conv["mapping"])
    elif isinstance(conv.get("messages"), list):
        # Some exports use a flat messages array
        for m in conv["messages"]:
            if not isinstance(m, dict):
                continue
            role = (m.get("role") or m.get("author", {}).get("role") or "").lower()
            text = ""
            content = m.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, dict):
                text = _flatten_parts(content.get("parts"))
            elif isinstance(content, list):
                text = _flatten_parts(content)
            if role and text.strip() and role != "system":
                turns.append({"role": role, "text": text.strip(), "ts": m.get("create_time")})

    if not turns:
        return None

    parts: list[str] = [f"# {title}"]
    if created or updated:
        meta = " | ".join(x for x in (
            f"created {created}" if created else "",
            f"updated {updated}" if updated else "",
        ) if x)
        if meta:
            parts.append(f"_{meta}_")
    parts.append("")
    for t in turns:
        label = _ROLE_LABEL.get(t["role"], t["role"].title())
        parts.append(f"**{label}:** {t['text']}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _looks_like_chatgpt_export(data: Any) -> bool:
    if not isinstance(data, list) or not data:
        return False
    first = data[0]
    if not isinstance(first, dict):
        return False
    return ("mapping" in first or "messages" in first) and "title" in first


# ── Anthropic / Claude export ─────────────────────────────────

_ANTHROPIC_ROLE = {"human": "User", "assistant": "Assistant"}


def _looks_like_anthropic_export(data: Any) -> bool:
    if not isinstance(data, list) or not data:
        return False
    first = data[0]
    if not isinstance(first, dict):
        return False
    return "chat_messages" in first and "name" in first


def _anthropic_conversation_to_md(conv: dict) -> str | None:
    title = (conv.get("name") or "Untitled").strip()
    created = conv.get("created_at", "")[:16].replace("T", " ") if conv.get("created_at") else ""
    updated = conv.get("updated_at", "")[:16].replace("T", " ") if conv.get("updated_at") else ""

    msgs = conv.get("chat_messages")
    if not isinstance(msgs, list) or not msgs:
        return None

    parts: list[str] = [f"# {title}"]
    if created or updated:
        meta = " | ".join(x for x in (
            f"created {created}" if created else "",
            f"updated {updated}" if updated else "",
        ) if x)
        if meta:
            parts.append(f"_{meta}_")
    parts.append("")

    has_content = False
    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        sender = (msg.get("sender") or "").lower()
        text = (msg.get("text") or "").strip()
        if not sender or not text:
            continue
        label = _ANTHROPIC_ROLE.get(sender, sender.title())
        parts.append(f"**{label}:** {text}")
        parts.append("")
        has_content = True

    return "\n".join(parts).rstrip() + "\n" if has_content else None


# ── Generic helpers: field sniffing ──────────────────────────

_TITLE_KEYS = ("title", "name", "subject", "heading", "label", "summary",
               "question", "topic", "description")
_ROLE_KEYS = ("role", "sender", "author", "from", "speaker", "actor", "type")
_TEXT_KEYS = ("text", "content", "body", "message", "value", "answer",
              "response", "input", "output", "data")
_TIME_KEYS = ("created_at", "create_time", "timestamp", "date", "time",
              "updated_at", "update_time", "created", "updated", "ts")

# Don't split tiny arrays — overhead > benefit
_MIN_ITEMS_TO_SPLIT = 3
# Items shorter than this aren't worth their own doc
_MIN_ITEM_CHARS = 50


def _find_str_field(obj: dict, candidates: tuple[str, ...]) -> str:
    """Return the first non-empty string value matching a candidate key."""
    for k in candidates:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _deep_text(val: Any, depth: int = 0) -> str:
    """Recursively extract text from a value. Handles nested dicts/lists."""
    if depth > 4:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, list):
        parts = []
        for item in val:
            t = _deep_text(item, depth + 1)
            if t:
                parts.append(t)
        return "\n".join(parts)
    if isinstance(val, dict):
        # Prefer known text fields, then fall back to all string values
        for k in _TEXT_KEYS:
            if k in val:
                t = _deep_text(val[k], depth + 1)
                if t:
                    return t
        parts = []
        for v in val.values():
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
        return "\n".join(parts)
    return ""


def _extract_role(msg: dict) -> str:
    """Extract role/sender from a message-like dict."""
    for k in _ROLE_KEYS:
        v = msg.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
        if isinstance(v, dict):
            # Nested: {"author": {"role": "user"}}
            for sub in ("role", "name", "type"):
                sv = v.get(sub)
                if isinstance(sv, str) and sv.strip():
                    return sv.strip().lower()
    return ""


def _extract_text(msg: dict) -> str:
    """Extract text content from a message-like dict."""
    for k in _TEXT_KEYS:
        v = msg.get(k)
        if v is None:
            continue
        t = _deep_text(v)
        if t and len(t.strip()) > 0:
            return t.strip()
    return ""


# ── Generic conversation detection ───────────────────────────

def _find_messages_field(obj: dict) -> str | None:
    """Find a key whose value looks like a messages array."""
    for key, val in obj.items():
        if not isinstance(val, list) or len(val) < 2:
            continue
        # Sample up to 3 items
        samples = [x for x in val[:5] if isinstance(x, dict)]
        if len(samples) < 2:
            continue
        has_role = sum(1 for s in samples if _extract_role(s)) >= len(samples) // 2
        has_text = sum(1 for s in samples if _extract_text(s)) >= len(samples) // 2
        if has_role and has_text:
            return key
    return None


def _generic_conversation_to_md(item: dict, msg_key: str) -> str | None:
    """Render any conversation-shaped item as markdown."""
    title = _find_str_field(item, _TITLE_KEYS)
    if not title:
        # Use first ~60 chars of first message as title
        msgs = item.get(msg_key, [])
        if msgs and isinstance(msgs[0], dict):
            title = _extract_text(msgs[0])[:60].rstrip()
            if len(title) == 60:
                title += "..."

    time_str = _find_str_field(item, _TIME_KEYS)

    parts: list[str] = [f"# {title or 'Untitled'}"]
    if time_str:
        parts.append(f"_{time_str}_")
    parts.append("")

    has_content = False
    for msg in item.get(msg_key, []):
        if not isinstance(msg, dict):
            continue
        role = _extract_role(msg)
        text = _extract_text(msg)
        if not text:
            continue
        label = _ROLE_LABEL.get(role, role.title() if role else "Unknown")
        parts.append(f"**{label}:** {text}")
        parts.append("")
        has_content = True

    return "\n".join(parts).rstrip() + "\n" if has_content else None


def _is_conversation_array(data: list[dict]) -> str | None:
    """Check if a list of dicts looks like conversations. Returns the
    messages field name if found, else None."""
    if len(data) < _MIN_ITEMS_TO_SPLIT:
        return None
    # Check first few items for a common messages field
    hits: dict[str, int] = {}
    for item in data[:10]:
        if not isinstance(item, dict):
            continue
        key = _find_messages_field(item)
        if key:
            hits[key] = hits.get(key, 0) + 1
    if not hits:
        return None
    best_key = max(hits, key=hits.get)
    # At least half the sampled items should have this pattern
    if hits[best_key] >= min(len(data), 10) // 2:
        return best_key
    return None


# ── Generic array splitting ──────────────────────────────────

def _item_to_md(item: dict, idx: int) -> str | None:
    """Render a generic dict item as markdown. Extracts title and text
    content heuristically."""
    title = _find_str_field(item, _TITLE_KEYS)
    time_str = _find_str_field(item, _TIME_KEYS)

    # Collect all meaningful text from the item
    text_parts: list[str] = []
    for key, val in item.items():
        if key in (*_TITLE_KEYS, *_TIME_KEYS):
            continue  # Already used above
        text = _deep_text(val)
        if text and len(text.strip()) > 10:
            text_parts.append(f"**{key}:** {text.strip()}")

    if not text_parts:
        # Fall back to compact JSON for items with no obvious text
        try:
            text_parts.append(_json.dumps(item, indent=2, ensure_ascii=False))
        except (TypeError, ValueError):
            return None

    parts: list[str] = []
    if title:
        parts.append(f"# {title}")
    else:
        parts.append(f"# Item {idx + 1}")
    if time_str:
        parts.append(f"_{time_str}_")
    parts.append("")
    parts.extend(text_parts)

    result = "\n".join(parts).rstrip() + "\n"
    return result if len(result) >= _MIN_ITEM_CHARS else None


def _split_array(data: list[dict], fname: str) -> list[ExtractedDoc] | None:
    """Split an array of dicts into per-item docs. Returns None if the
    array is too small or items are too sparse to be worth splitting."""
    if len(data) < _MIN_ITEMS_TO_SPLIT:
        return None
    # Verify most items are dicts
    dict_items = [x for x in data if isinstance(x, dict)]
    if len(dict_items) < len(data) // 2:
        return None

    docs: list[ExtractedDoc] = []
    for i, item in enumerate(dict_items):
        md = _item_to_md(item, i)
        if md:
            title = _find_str_field(item, _TITLE_KEYS) or f"Item {i + 1}"
            docs.append(ExtractedDoc(
                text=md,
                mime="text/markdown",
                metadata={
                    "source_format": "json_array",
                    "item_title": title,
                    "item_index": i,
                },
                chunk_mode="prose",
            ))
    return docs if docs else None


# ── Object walker: find all splittable arrays at any depth ───

def _find_arrays(obj: Any, path: str = "", depth: int = 0,
                 max_depth: int = 6) -> list[tuple[str, list[dict]]]:
    """Walk a JSON value recursively. Return all (dotted_path, items) pairs
    where *items* is a list of dicts long enough to split."""
    if depth > max_depth:
        return []

    found: list[tuple[str, list[dict]]] = []

    if isinstance(obj, dict):
        for key, val in obj.items():
            child_path = f"{path}.{key}" if path else key
            if isinstance(val, list):
                dict_items = [x for x in val if isinstance(x, dict)]
                if len(dict_items) >= _MIN_ITEMS_TO_SPLIT and len(dict_items) >= len(val) // 2:
                    found.append((child_path, dict_items))
                # Don't recurse *into* a useful array's items — they'll be
                # split individually. Only recurse into arrays we skipped.
                elif isinstance(val, list):
                    for item in val:
                        found.extend(_find_arrays(item, child_path, depth + 1, max_depth))
            elif isinstance(val, dict):
                found.extend(_find_arrays(val, child_path, depth + 1, max_depth))

    return found


def _process_array(path: str, items: list[dict], fname: str) -> list[ExtractedDoc]:
    """Try conversation detection first, fall back to generic item split."""
    msg_key = _is_conversation_array(items)
    if msg_key:
        docs: list[ExtractedDoc] = []
        for item in items:
            md = _generic_conversation_to_md(item, msg_key)
            if md:
                title = _find_str_field(item, _TITLE_KEYS) or "Untitled"
                docs.append(ExtractedDoc(
                    text=md,
                    mime="text/markdown",
                    metadata={
                        "source_format": "json_conversations",
                        "conversation_title": title,
                        "json_path": path,
                    },
                    chunk_mode="prose",
                ))
        if docs:
            return docs

    split = _split_array(items, fname)
    if split:
        for doc in split:
            doc.metadata["json_path"] = path
        return split
    return []


def _split_object(data: dict, fname: str) -> list[ExtractedDoc] | None:
    """Walk the full object tree, find every array of dicts, and split
    each one into individual docs."""
    arrays = _find_arrays(data)
    if not arrays:
        return None

    all_docs: list[ExtractedDoc] = []
    for path, items in arrays:
        all_docs.extend(_process_array(path, items, fname))

    return all_docs if all_docs else None


# ── Entry point ──────────────────────────────────────────────

def extract(path: str) -> ExtractorResult:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        data = _json.loads(raw)
    except _json.JSONDecodeError as e:
        raise ExtractionError(f"invalid JSON: {e}")
    except OSError as e:
        raise ExtractionError(f"read failed: {e}")

    fname = os.path.basename(path)

    # ── 1. Known formats (specific structure detection) ──

    if _looks_like_chatgpt_export(data):
        docs: list[ExtractedDoc] = []
        for conv in data:
            if not isinstance(conv, dict):
                continue
            md = _conversation_to_md(conv)
            if md:
                title = (conv.get("title") or "Untitled").strip()
                docs.append(ExtractedDoc(
                    text=md,
                    mime="text/markdown",
                    metadata={
                        "source_format": "chatgpt_export",
                        "conversation_title": title,
                    },
                    chunk_mode="prose",
                ))
        if docs:
            return docs

    if _looks_like_anthropic_export(data):
        docs = []
        for conv in data:
            if not isinstance(conv, dict):
                continue
            md = _anthropic_conversation_to_md(conv)
            if md:
                title = (conv.get("name") or "Untitled").strip()
                docs.append(ExtractedDoc(
                    text=md,
                    mime="text/markdown",
                    metadata={
                        "source_format": "anthropic_export",
                        "conversation_title": title,
                    },
                    chunk_mode="prose",
                ))
        if docs:
            return docs

    # ── 2. Generic arrays ──

    if isinstance(data, list) and data:
        dict_items = [x for x in data if isinstance(x, dict)]

        if len(dict_items) >= _MIN_ITEMS_TO_SPLIT:
            # 2a. Conversation-shaped array?
            msg_key = _is_conversation_array(dict_items)
            if msg_key:
                docs = []
                for item in dict_items:
                    md = _generic_conversation_to_md(item, msg_key)
                    if md:
                        title = _find_str_field(item, _TITLE_KEYS) or "Untitled"
                        docs.append(ExtractedDoc(
                            text=md,
                            mime="text/markdown",
                            metadata={
                                "source_format": "json_conversations",
                                "conversation_title": title,
                            },
                            chunk_mode="prose",
                        ))
                if docs:
                    return docs

            # 2b. Generic object array — split per item
            split = _split_array(dict_items, fname)
            if split:
                return split

    # ── 3. Object with array properties at any depth ──

    if isinstance(data, dict):
        split = _split_object(data, fname)
        if split:
            return split

    # ── 4. Fallback: single pretty-printed doc ──
    try:
        pretty = _json.dumps(data, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        pretty = raw
    return ExtractedDoc(
        text=f"[{fname}]\n{pretty}",
        mime="application/json",
        metadata={"source_format": "json"},
        chunk_mode="prose",
    )


register_ext(".json", extract)
