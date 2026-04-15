"""JSON extractor with ChatGPT-export auto-detection.

Generic .json files become pretty-printed text. ChatGPT data exports
(`conversations.json`, `conversations-NNN.json`) are reconstructed into
clean Markdown transcripts so the chunker can split them at conversation
boundaries.
"""

from __future__ import annotations

import json as _json
import os
from datetime import datetime, timezone
from typing import Any

from . import ExtractedDoc, ExtractionError, register_ext


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


# ── Entry point ──────────────────────────────────────────────

def extract(path: str) -> ExtractedDoc:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        data = _json.loads(raw)
    except _json.JSONDecodeError as e:
        raise ExtractionError(f"invalid JSON: {e}")
    except OSError as e:
        raise ExtractionError(f"read failed: {e}")

    fname = os.path.basename(path)

    # ── ChatGPT data export ──
    if _looks_like_chatgpt_export(data):
        sections: list[str] = [f"[ChatGPT export · {fname}]"]
        kept = 0
        for conv in data:
            if not isinstance(conv, dict):
                continue
            md = _conversation_to_md(conv)
            if md:
                sections.append(md)
                sections.append("\n---\n")
                kept += 1
        if kept == 0:
            raise ExtractionError("no conversations with content")
        text = "\n".join(sections).strip() + "\n"
        return ExtractedDoc(
            text=text,
            mime="text/markdown",
            metadata={"source_format": "chatgpt_export", "conversations": kept},
            chunk_mode="prose",
        )

    # ── Generic JSON: pretty-print as text ──
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
