"""How a persona sits in each client's folder (ADR 0171).

The registry keeps a persona as one Markdown file with YAML front matter
(``PERSONA.md``). Claude Code and Cursor read that file as it is. Codex reads
a custom agent as a TOML file with ``name``, ``description`` and
``developer_instructions``: ``rendered`` writes a persona that way, and
``parsed`` takes a file edited in Codex's folder back into the persona.

Settings meant for Codex alone, such as its ``model`` or
``model_reasoning_effort``, ride in the persona's front matter under
``codex:`` and are written as top-level keys of the TOML file. Only strings,
numbers and true/false values carry over; Claude Code and Cursor ignore the
key. Skills sit in every folder as they are.
"""
from __future__ import annotations

import math
import re
import tomllib

from . import registry

MARKDOWN = 'markdown'
CODEX = 'codex'
FORMATS = (MARKDOWN, CODEX)
# A persona's file name in a folder of each format, after its name.
SUFFIX = {MARKDOWN: '.md', CODEX: '.toml'}
CODEX_KEY = 'codex'
_FIELDS = ('name', 'description', 'developer_instructions')
_BARE = re.compile(r'[A-Za-z0-9_-]+')
_HEADER = '# A persona Imprint keeps: edits here reach your other clients and devices.'


def rendered(kind: str, fmt: str, files: dict[str, str]) -> dict[str, str]:
    """An item's files as they sit in a folder of that format."""
    if kind != 'persona' or fmt != CODEX:
        return files
    main = registry.MAIN['persona']
    return {main: codex_agent(files[main])}


def parsed(kind: str, fmt: str, files: dict[str, str], base: dict[str, str] | None = None) -> dict[str, str]:
    """The item's files from what a folder of that format holds. ``base`` is
    the item's current files: what Codex's file cannot hold (Claude Code's
    ``tools``, say) is kept from it. ValueError when the file cannot be
    read as an item."""
    if kind != 'persona' or fmt != CODEX:
        return files
    main = registry.MAIN['persona']
    return {main: persona_from_codex(files[main], base.get(main) if base else None)}


# ── Persona → Codex agent ─────────────────────────────────────────

def _scalar(value) -> bool:
    if isinstance(value, bool) or isinstance(value, (int, str)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _string(text: str) -> str:
    """A TOML basic string."""
    escaped = []
    for char in text:
        if char == '\\':
            escaped.append('\\\\')
        elif char == '"':
            escaped.append('\\"')
        elif char == '\n':
            escaped.append('\\n')
        elif char == '\t':
            escaped.append('\\t')
        elif ord(char) < 32 or ord(char) == 127:
            escaped.append(f'\\u{ord(char):04X}')
        else:
            escaped.append(char)
    return '"' + ''.join(escaped) + '"'


def _value(value) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return _string(value)
    return repr(value)


def _multiline(text: str) -> str:
    """A TOML multi-line string holding ``text``, which ends with a newline:
    literal (verbatim) when it can be, else basic with escapes."""
    literal_safe = "'''" not in text and not any(
        (ord(c) < 32 and c not in '\t\n\r') or ord(c) == 127 for c in text) and '\r' not in text.replace('\r\n', '')
    if literal_safe:
        return "'''\n" + text + "'''"
    escaped = []
    for char in text:
        if char == '\\':
            escaped.append('\\\\')
        elif char == '"':
            escaped.append('\\"')
        elif char in '\n\t':
            escaped.append(char)
        elif ord(char) < 32 or ord(char) == 127:
            escaped.append(f'\\u{ord(char):04X}')
        else:
            escaped.append(char)
    return '"""\n' + ''.join(escaped) + '"""'


def codex_agent(text: str) -> str:
    """A persona's ``PERSONA.md`` as a Codex custom agent file."""
    meta, body = registry.front_matter(text)
    lines = [_HEADER, f'name = {_string(str(meta["name"]))}',
             f'description = {_string(" ".join(str(meta.get("description") or "").split()))}']
    extra = meta.get(CODEX_KEY)
    if isinstance(extra, dict):
        for key, value in extra.items():
            if isinstance(key, str) and _BARE.fullmatch(key) and key not in _FIELDS and _scalar(value):
                lines.append(f'{key} = {_value(value)}')
    lines.append(f'developer_instructions = {_multiline(body.strip() + chr(10))}')
    return '\n'.join(lines) + '\n'


# ── Codex agent → persona ─────────────────────────────────────────

def persona_from_codex(text: str, base: str | None = None) -> str:
    """A Codex custom agent file as a persona's ``PERSONA.md``. The fields
    Codex does not hold come from ``base`` (the persona's current file),
    whose front matter stays as it was written when only the instructions
    changed. ValueError naming what cannot be kept."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f'is not valid TOML ({error})') from None
    for field in _FIELDS:
        if not isinstance(data.get(field), str):
            raise ValueError(f'needs {field} as a string')
    extra = {}
    for key, value in data.items():
        if key in _FIELDS:
            continue
        if not _scalar(value):
            raise ValueError(f'sets {key}, which a persona cannot carry: only text, number and true/false settings '
                             'carry over from Codex')
        extra[key] = value
    instructions = data['developer_instructions'].strip()
    meta, body, head = {}, '', ''
    if base:
        meta, body = registry.front_matter(base)
        head = base[:len(base) - len(body)]
    if (head and meta.get('name') == data['name'] and (meta.get(CODEX_KEY) or {}) == extra
            and ' '.join(str(meta.get('description') or '').split()) == ' '.join(data['description'].split())):
        return head + ('\n' if body.startswith(('\n', '\r\n')) else '') + instructions + '\n'
    import yaml
    fields = {'name': data['name'], 'description': data['description']}
    fields.update((k, v) for k, v in meta.items() if k not in ('name', 'description', CODEX_KEY))
    if extra:
        fields[CODEX_KEY] = extra
    front = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True, width=10_000)
    return f'---\n{front}---\n\n{instructions}\n'
