"""EML (.eml) and MBOX (.mbox) extractors using only the stdlib."""

from __future__ import annotations

import email
import mailbox
import os
from email import policy
from email.parser import BytesParser

from . import ExtractedDoc, ExtractionError, register_ext, register_mime


def _msg_to_text(msg: email.message.EmailMessage) -> tuple[str, dict]:
    meta = {}
    for h in ("From", "To", "Cc", "Subject", "Date"):
        v = msg.get(h)
        if v:
            meta[h.lower()] = str(v)

    body_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            if ctype == "text/plain":
                try:
                    body_parts.append(part.get_content())
                except Exception:
                    try:
                        body_parts.append(
                            part.get_payload(decode=True).decode(
                                part.get_content_charset() or "utf-8", errors="ignore"
                            )
                        )
                    except Exception:
                        continue
            elif ctype == "text/html" and not body_parts:
                try:
                    from bs4 import BeautifulSoup  # type: ignore
                    html = part.get_content()
                    body_parts.append(BeautifulSoup(html, "html.parser").get_text("\n"))
                except Exception:
                    continue
    else:
        try:
            body_parts.append(msg.get_content())
        except Exception:
            try:
                body_parts.append(
                    msg.get_payload(decode=True).decode(
                        msg.get_content_charset() or "utf-8", errors="ignore"
                    )
                )
            except Exception:
                pass

    header_text = "\n".join(f"{k.title()}: {v}" for k, v in meta.items())
    body = "\n\n".join(p.strip() for p in body_parts if p and p.strip())
    text = f"{header_text}\n\n{body}".strip()
    return text, meta


def _extract_eml_bytes(data: bytes, source_url: str = "") -> ExtractedDoc:
    try:
        msg = BytesParser(policy=policy.default).parsebytes(data)
    except Exception as e:
        raise ExtractionError(f"eml parse failed: {e}") from e
    text, meta = _msg_to_text(msg)
    return ExtractedDoc(
        text=text,
        mime="message/rfc822",
        metadata=meta,
        chunk_mode="prose",
    )


def _extract_eml(path: str) -> ExtractedDoc:
    with open(path, "rb") as f:
        doc = _extract_eml_bytes(f.read())
    doc.metadata.setdefault("filename", os.path.basename(path))
    return doc


def _extract_mbox(path: str) -> ExtractedDoc:
    try:
        mbox = mailbox.mbox(path)
    except Exception as e:
        raise ExtractionError(f"mbox open failed: {e}") from e

    messages = []
    count = 0
    for msg in mbox:
        # Re-parse via BytesParser for EmailMessage policy (richer API).
        try:
            raw = bytes(msg)
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
            text, _ = _msg_to_text(parsed)
            if text:
                messages.append(text)
                count += 1
        except Exception:
            continue

    return ExtractedDoc(
        text="\n\n---\n\n".join(messages),
        mime="application/mbox",
        metadata={"filename": os.path.basename(path), "message_count": count},
        chunk_mode="prose",
    )


register_ext(".eml", _extract_eml)
register_ext(".mbox", _extract_mbox)
register_mime("message/rfc822", _extract_eml_bytes)
