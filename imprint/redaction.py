"""Secrets replaced with placeholders before memory text is stored (ADR 0060).

Every memory and fact passes through :func:`redact_all` on its way into
storage, before its logical ID is computed and its text embedded, so a
credential never reaches Qdrant, the graph database or a sync stream. A
secret becomes ``[REDACTED:<kind>]``; neither the value nor a hash of it is
kept. The placeholders are the audit record: :func:`kinds` counts them.

Deterministic rules always run. They match only known credential formats
and literal values assigned to credential-named keys, so ordinary code,
hashes, UUIDs and encoded data stay intact. Several token formats follow
gitleaks' rule definitions (MIT, licenses/gitleaks/LICENSE).

``redact.privacy_filter`` adds OpenAI Privacy Filter's ``secret`` and
``account_number`` spans (:mod:`imprint.privacy_filter`). When it is on and
cannot run, the write fails: storing text less redacted than configured is
not an option, and the caller still holds its source to retry.

Placeholder names are part of stored content and so of memory IDs; renaming
a kind changes the IDs of memories redacted under it.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_PLACEHOLDER = re.compile(r"\[REDACTED:([a-z0-9-]+)\]")


def placeholder(kind: str) -> str:
    return f"[REDACTED:{kind}]"


# ── Known credential formats ────────────────────────────────────
# A token never starts or ends inside a longer run of its own alphabet.
_START = r"(?<![A-Za-z0-9_-])"
_END = r"(?![A-Za-z0-9_-])"
_PEM_LABEL = r"(?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?"
# Key bodies wrap with newlines, or with escaped newlines inside JSON and
# .env strings. Runs are possessive and separated by gaps, so matching stays
# linear.
_GAP = r"(?:\\[rn]|\s)++"
_RUN = r"[A-Za-z0-9+/=]"
_BODY = rf"(?:{_RUN}{{16,}}+|(?:Proc-Type|DEK-Info|Comment):[^\n]*+)"
# A whole block; a block cut off by a chunk boundary, up to the end of the
# text or through its base64 lines.
_PRIVATE_KEY = re.compile(rf"-----BEGIN {_PEM_LABEL}-----(?:[\s\S]*?-----END {_PEM_LABEL}-----"
                          rf"|(?:{_GAP}{_RUN}++)*+(?:\\[rn]|\s)*+\Z|(?:{_GAP}{_BODY})*+)")
# The rest of a block that began in the previous chunk: the base64 lines
# before its END line, read backwards from it, back to the start of the text
# for a first line the chunk boundary cut.
_KEY_END = re.compile(rf"-----END {_PEM_LABEL}-----")
_KEY_TAIL_REVERSED = re.compile(rf"(?:(?:n\\|r\\|\s)++{_RUN}{{16,}}+)*+(?:(?:n\\|r\\|\s)++{_RUN}++\Z)?")


def _private_key_spans(text: str):
    for match in _PRIVATE_KEY.finditer(text):
        yield match.start(), match.end(), "private-key"
    for end in _KEY_END.finditer(text):
        tail = _KEY_TAIL_REVERSED.match(text[end.start() - 1::-1] if end.start() else "")
        if tail.end():
            yield end.start() - tail.end(), end.end(), "private-key"


TOKEN_RULES: tuple[tuple[str, str], ...] = (
    ("aws-access-key", r"(?<![A-Za-z0-9])(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}(?![A-Za-z0-9])"),
    ("github-token", rf"{_START}(?:gh[pousr]_[A-Za-z0-9]{{36,251}}|github_pat_[A-Za-z0-9_]{{82}}){_END}"),
    ("gitlab-token", rf"{_START}gl(?:pat|dt|ptt|rt|cbt|imt|soat|oas)-[A-Za-z0-9_-]{{20,}}(?:\.[A-Za-z0-9_-]+)*{_END}"),
    ("anthropic-key", rf"{_START}sk-ant-[a-z]{{2,8}}\d{{2}}-[A-Za-z0-9_-]{{40,}}{_END}"),
    ("openai-key", rf"{_START}(?:sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{{20,}}|sk-[A-Za-z0-9]{{20}}T3BlbkFJ[A-Za-z0-9]{{20}}){_END}"),
    ("huggingface-token", rf"{_START}(?:hf_|api_org_)[A-Za-z0-9]{{34}}{_END}"),
    ("slack-token", rf"{_START}(?:xoxe\.)?(?:xox[abposer]|xapp)-\d[A-Za-z0-9-]{{8,}}{_END}"),
    ("slack-webhook", r"https://hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9+/]{20,}"),
    ("stripe-key", rf"{_START}(?:(?:sk|rk)_(?:test|live|prod)_[A-Za-z0-9]{{10,99}}|whsec_[A-Za-z0-9]{{24,}}){_END}"),
    ("google-api-key", rf"{_START}AIza[A-Za-z0-9_-]{{35}}{_END}"),
    ("google-oauth-secret", rf"{_START}GOCSPX-[A-Za-z0-9_-]{{28}}{_END}"),
    ("npm-token", rf"{_START}npm_[A-Za-z0-9]{{36}}{_END}"),
    ("pypi-token", rf"{_START}pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{{50,}}{_END}"),
    ("sendgrid-key", rf"{_START}SG\.[A-Za-z0-9_-]{{22}}\.[A-Za-z0-9_-]{{43}}{_END}"),
    ("jwt", rf"{_START}eyJ[A-Za-z0-9_-]{{8,}}\.eyJ[A-Za-z0-9_-]{{8,}}\.[A-Za-z0-9_-]{{8,}}{_END}"),
)

# ── Credentials by position ─────────────────────────────────────
# The redacted span is the group named "s"; candidates pass a shape check.
_URL_PASSWORD = re.compile(
    r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{1,20}://[^\s:/?#@'\"<>]{0,128}:(?P<s>[^\s/?#@'\"<>]{1,256})@")
_BEARER = re.compile(r"(?i:\bbearer[ \t]+)(?P<s>[A-Za-z0-9._~+/-]{20,}=*)")
_BASIC = re.compile(r"(?i:\bauthorization[\"']?[ \t]*[:=][ \t]*[\"']?basic[ \t]+)(?P<s>[A-Za-z0-9+/]{8,}={0,2})")
# A credential-named key (the keyword ends the name) and the literal assigned
# to it. Quoted literals may hold any non-space characters; unquoted ones end
# at the first quote, comma, semicolon or space.
_PASSWORD_WORDS = r"password|passwd|passphrase|pwd"
_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_.-])(?P<key>(?i:[A-Za-z0-9_.-]*?(?:" + _PASSWORD_WORDS +
    r"|secret|token|api[_-]?key|apikey|access[_-]?key|secret[_-]?key|private[_-]?key|signing[_-]?key"
    r"|encryption[_-]?key|master[_-]?key|account[_-]?key|sharedaccesskey|client[_-]?secret|credentials?)))"
    r"[\"']?[ \t]*(?:=>|:=|=|:)[ \t]*"
    r"(?:(?P<q>[\"'`])(?P<qs>[^\s\"'`]{1,512})(?P=q)|(?P<s>[^\s\"'`,;]{1,512}))")
_PASSWORD_KEY = re.compile(_PASSWORD_WORDS, re.I)
_PLACEHOLDER_VALUE = re.compile(
    r"^(?:[$%{<*]|\[REDACTED:)|\$\{|\{\{|^(?:x+|\*+|\.+|-+|_+|#+|0+|password|passwd|pass|pwd|secret|token)$|://"
    r"|^(?:[~.]{0,2}/|[A-Za-z]:[\\/])", re.I)
_EXPRESSION = re.compile(r"[()\[\]{}<>]|^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")


def _entropy(value: str) -> float:
    counts = Counter(value)
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def _password_like(value: str) -> bool:
    """A literal assigned to a password key: not a single plain word."""
    return len(value) >= 6 and (len(value) >= 12 or not value.isalpha())


def _token_like(value: str) -> bool:
    """A literal assigned to a token/key/secret name: long, mixed and random."""
    return (len(value) >= 12 and any(c.isdigit() for c in value) and any(c.isalpha() for c in value)
            and _entropy(value) >= 3.0)


def _assignment_spans(text: str):
    for match in _ASSIGNMENT.finditer(text):
        quoted = match.group("qs") is not None
        value = match.group("qs") if quoted else match.group("s")
        if _PLACEHOLDER_VALUE.search(value) or (not quoted and _EXPRESSION.search(value)):
            continue
        password = bool(_PASSWORD_KEY.search(match.group("key")))
        if password and _password_like(value) or not password and _token_like(value):
            group = "qs" if quoted else "s"
            yield match.start(group), match.end(group), "password" if password else "secret"


def _positional_spans(text: str):
    for pattern, kind, check in ((_URL_PASSWORD, "password", None), (_BEARER, "bearer-token", _token_like),
                                 (_BASIC, "basic-auth", None)):
        for match in pattern.finditer(text):
            value = match.group("s")
            if not _PLACEHOLDER_VALUE.search(value) and (check is None or check(value)):
                yield match.start("s"), match.end("s"), kind
    yield from _assignment_spans(text)


_TOKENS = tuple((kind, re.compile(pattern)) for kind, pattern in TOKEN_RULES)


def rule_spans(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, kind)`` of every credential the rules find."""
    spans = list(_private_key_spans(text))
    spans.extend((m.start(), m.end(), kind) for kind, pattern in _TOKENS for m in pattern.finditer(text))
    spans.extend(_positional_spans(text))
    return spans


# ── Applying spans ──────────────────────────────────────────────
def _outside_placeholders(text: str, spans):
    """Spans minus the placeholders already in ``text``, so redacting
    redacted text changes nothing. Pieces without a letter or digit go."""
    taken = [m.span() for m in _PLACEHOLDER.finditer(text)]
    for start, end, kind in spans:
        pieces = [(start, end)]
        for left, right in taken:
            pieces = [part for a, b in pieces
                      for part in ((a, min(b, left)), (max(a, right), b)) if part[0] < part[1]]
        for a, b in pieces:
            piece = text[a:b]
            stripped = piece.strip()
            if any(c.isalnum() for c in stripped):
                offset = a + piece.index(stripped)
                yield offset, offset + len(stripped), kind


def apply(text: str, spans) -> str:
    """``text`` with ``spans`` replaced by placeholders. Overlapping spans
    merge under the kind of the earliest (rule order breaks ties)."""
    merged: list[list] = []
    for start, end, kind in sorted(_outside_placeholders(text, spans), key=lambda s: (s[0], -s[1])):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, kind])
    for start, end, kind in reversed(merged):
        text = text[:start] + placeholder(kind) + text[end:]
    return text


def redact_all(texts: list[str]) -> list[str]:
    """``texts`` with their secrets replaced (ADR 0060)."""
    out = [apply(text, rule_spans(text)) if text else text for text in texts]
    from .config_schema import resolve
    if not resolve("redact.privacy_filter")[0]:
        return out
    from . import privacy_filter
    try:
        return [apply(text, privacy_filter.spans(text)) if text.strip() else text for text in out]
    except Exception as error:
        raise RuntimeError(
            f"Privacy Filter is on (redact.privacy_filter) but could not run: {error}. Nothing was stored. "
            "Fix the model download, or turn it off: imprint config set redact.privacy_filter false") from error


def redact(text: str) -> str:
    return redact_all([text])[0]


def kinds(text: str) -> Counter:
    """How many placeholders of each kind ``text`` holds."""
    return Counter(_PLACEHOLDER.findall(text))
