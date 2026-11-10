"""Skills and personas for every MCP client (ADR 0170).

Clients that read skill and subagent folders get the registry's items as
files (ADRs 0169, 0171). Every other MCP client, and a WSL session connected
to a Windows runtime, reaches them through Imprint's server: the handshake
instructions list each active item's name and description (``index``), and
the ``skills`` and ``skill_edit`` tools read, create and change them
(``listing``, ``read``, ``create``, ``edit``). A change is written into the
clients' folders at once and reaches paired devices with the next sync.
"""
from __future__ import annotations

import os

from . import registry

# Estimated tokens the handshake's list may take; descriptions are cut to
# DESCRIPTION_CHARS there and listed in full by skills(action="list").
INDEX_BUDGET = 1500
CHARS_PER_TOKEN = 4
DESCRIPTION_CHARS = 250
# Characters one read returns; ``start`` pages through a longer file.
READ_CHARS = 20_000
# Clients that read the folders Imprint writes on this computer, by the name
# their MCP handshake gives, with their label in the clients table. Codex and
# Cursor names are what their MCP clients send; a name not listed here gets
# the list, which at worst repeats what the client already shows.
NATIVE = {'claude-code': 'Claude Code', 'codex-mcp-client': 'Codex', 'cursor-vscode': 'Cursor',
          'opencode': 'OpenCode'}
WRITE_STATES = ('active', 'disabled')


def native_kinds(client: str | None) -> set[str]:
    """The kinds of item this client loads from its own folders on this
    computer, as the last write into the clients' folders reported. A WSL
    session served by a Windows runtime reads none of them."""
    label = NATIVE.get(client or '')
    if not label or os.environ.get('IMPRINT_WSL_DISTRO'):
        return set()
    from . import registry_files
    report = registry_files.last_report() or {}
    return {folder.get('kind') for folder in report.get('folders') or []
            if isinstance(folder, dict) and label in (folder.get('clients') or [])}


def _short(text: str, limit: int) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def index(client: str | None = None) -> str:
    """The handshake's block listing the active skills and personas, or
    empty when there are none the client lacks."""
    if not registry.exists():
        return ''
    skip = native_kinds(client)
    sections = [(kind, items) for kind in registry.KINDS if kind not in skip
                for items in [registry.active(kind)] if items]
    if not sections:
        return ''
    head = ("\n\n# The user's skills and personas\n"
            "The user keeps these in Imprint, the same in every client and on every device. A skill is "
            "instructions for a kind of task; a persona is a role to hand work to or take on. When one's "
            'description fits the task, load it with skills(action="get", name="<name>") and follow it. If your '
            "client already lists one by that name, it is the same: use it there.\n")
    budget = INDEX_BUDGET * CHARS_PER_TOKEN - len(head)
    lines: list[str] = []
    left = sum(len(items) for _, items in sections)
    for kind, items in sections:
        title = 'Skills:' if kind == 'skill' else 'Personas:'
        if budget < len(title) + 1:
            break
        lines.append(title)
        budget -= len(title) + 1
        for item in sorted(items, key=lambda i: i['name']):
            line = f"- {item['name']}: {_short(item['description'], DESCRIPTION_CHARS)}"
            if len(line) + 1 > budget:
                line = f"- {item['name']}"
                if len(line) + 1 > budget:
                    break
            lines.append(line)
            budget -= len(line) + 1
            left -= 1
    if left:
        lines.append(f'- and {left} more: skills(action="list")')
    return head + '\n'.join(lines) + '\n'


# ── Reads ─────────────────────────────────────────────────────────

def _find(name: str, kind: str = '') -> dict:
    if not name:
        raise ValueError('name is required: skills(action="list") lists them')
    if kind and kind not in registry.KINDS:
        raise ValueError(f'kind must be one of {", ".join(registry.KINDS)}')
    if not registry.exists():
        raise ValueError(f'No {kind or "skill or persona"} named {name}')
    try:
        return registry.find(name, kind or None)
    except KeyError:
        raise ValueError(f'No {kind or "skill or persona"} named {name}') from None


def listing(kind: str = '') -> str:
    """Every skill and persona that is not removed, with its description."""
    if kind and kind not in registry.KINDS:
        raise ValueError(f'kind must be one of {", ".join(registry.KINDS)}')
    items = [i for i in registry.list_all(kind or None) if i['state'] != 'retired'] if registry.exists() else []
    if not items:
        return ('No skills or personas yet. skill_edit(action="create", ...) keeps one, or the user adds them '
                'with `imprint skills add` or on the dashboard\'s Skills page.')
    lines = []
    for item in items:
        marks = [mark for mark, on in (('disabled', item['state'] == 'disabled'),
                                       ('shadowed by an older one of the same name', item['shadowed'])) if on]
        files = '' if item['paths'] == [registry.MAIN[item['kind']]] else f" ({len(item['paths'])} files)"
        lines.append(f"{item['kind']} {item['name']}{files}{' [' + ', '.join(marks) + ']' if marks else ''}: "
                     f"{item['description']}")
    return '\n'.join(lines) + '\n\nLoad one with skills(action="get", name="<name>").'


def read(name: str, kind: str = '', path: str = '', start: int = 0) -> str:
    """An item's main file, or another of its files, from ``start``."""
    item = _find(name, kind)
    files = item['files']
    path = path or registry.MAIN[item['kind']]
    if path not in files:
        raise ValueError(f"{item['kind']} {item['name']} has no file {path}; it has {', '.join(files)}")
    text = files[path]
    start = max(0, int(start or 0))
    part = text[start:start + READ_CHARS]
    end = start + len(part)
    head = [f"{item['kind']} {item['name']} ({item['state']}), {path}"]
    others = [p for p in files if p != path]
    if others:
        head.append(f'Its other files: {", ".join(others)} (path="<file>" reads one)')
    if start or end < len(text):
        head.append(f'Characters {start:,}–{end:,} of {len(text):,}'
                    + (f'; next_start={end}' if end < len(text) else ''))
    return '\n'.join(head) + '\n\n' + part


# ── Writes ────────────────────────────────────────────────────────

def _rewrite(text: str, description: str = '', instructions: str = '') -> str:
    """The main file with a new description or instructions; the rest of
    its front matter stays as written."""
    meta, body = registry.front_matter(text)
    head = text[:len(text) - len(body)]
    if description:
        import yaml
        meta = {**meta, 'description': ' '.join(description.split())}
        head = '---\n' + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=10_000) + '---\n'
    if instructions:
        body = '\n' + instructions.strip() + '\n'
    return head + body


def _written(item: dict) -> str:
    """Write the change into the clients' folders now and say where it went."""
    from . import registry_files
    try:
        report = registry_files.apply()
    except Exception as error:  # noqa: BLE001 - the change is kept; the dashboard's next pass writes it
        return f'Not written to the clients yet ({error}); the next pass writes it.'
    name = item['name']
    mine = [p for p in report.get('written', []) + report.get('removed', [])
            if os.path.basename(p) in (name, f'{name}.md', f'{name}.toml')]
    problems = [f"{p['path']}: {p['problem']}" for p in report.get('problems', [])
                if os.path.basename(str(p.get('path', ''))) in (name, f'{name}.md', f'{name}.toml')]
    lines = [f'Written to {", ".join(mine)}.' if mine else 'The clients\' folders already held it.']
    if item['state'] == 'disabled':
        lines = ['Disabled: taken out of the clients\' folders.']
    if not report.get('folders'):
        lines = ['No connected client reads skill or persona folders on this computer; MCP clients reach it '
                 'through skills().']
    return '\n'.join(lines + [f'Left as it was: {problem}' for problem in problems])


def create(kind: str, name: str = '', description: str = '', instructions: str = '',
           files: dict | None = None) -> str:
    """Keep a new skill or persona."""
    if kind not in registry.KINDS:
        raise ValueError(f'kind must be one of {", ".join(registry.KINDS)}')
    main = registry.MAIN[kind]
    files = {path: text for path, text in (files or {}).items() if text is not None}
    if main not in files:
        if not (name and description and instructions):
            raise ValueError(f'A new {kind} needs name, description and instructions, or files with its {main}')
        files[main] = registry.compose(kind, name, description, instructions)[main]
    item = registry.create(kind, files)
    return (f"Kept {kind} {item['name']} [{item['id']}]; it reaches the user's paired devices with the next sync.\n"
            + _written(item))


def edit(name: str, kind: str = '', description: str = '', instructions: str = '',
         files: dict | None = None, state: str = '') -> str:
    """Change an item's files, description or instructions, or enable or
    disable it. A file mapped to null is removed from a skill."""
    if state and state not in WRITE_STATES:
        raise ValueError('state must be active or disabled; the user removes items in Imprint')
    item = _find(name, kind)
    main = registry.MAIN[item['kind']]
    changed = dict(item['files'])
    for path, text in (files or {}).items():
        if text is None:
            if path == main:
                raise ValueError(f'{main} cannot be removed')
            if changed.pop(path, None) is None:
                raise ValueError(f"{item['kind']} {item['name']} has no file {path}")
        else:
            changed[path] = text
    if description or instructions:
        changed[main] = _rewrite(changed[main], description, instructions)
    if not (files or description or instructions or state):
        raise ValueError('Nothing to change: pass files, description, instructions or state')
    updated = registry.update(item['id'], files=changed if changed != item['files'] else None, state=state or None)
    if updated['hash'] == item['hash'] and updated['state'] == item['state']:
        return f"{item['kind']} {item['name']} already reads that way; nothing changed."
    return (f"Changed {updated['kind']} {updated['name']}; the replaced version stays in this device's history, "
            "and the change reaches paired devices with the next sync.\n" + _written(updated))
