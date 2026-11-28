"""What a running agent is doing, read from the tail of its client's own
transcript (ADR 0233).

Claude Code and Codex append one JSON record per line as a session runs. A
:class:`Tail` reads only the bytes added since its last read, keeps a line
that is still being written for the next one, and folds each record into a
small state: the model, token totals, the context in use, the tool calls in
flight and the last few finished, and whether a turn is running. Activity
labels come from rules over the latest tool call (:func:`classify`); no
model is asked. Nothing read is stored or sent anywhere: the state lives in
the dashboard process and is dropped when nobody has asked for it a while.

Claude Code writes each content block of a reply as its own line, repeating
the reply's usage, so tokens are counted once per message id. Codex reports
running totals in ``token_count`` events, with the model's context window
and its rate-limit use.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .redaction import redact

ACTIONS = 3                    # finished tool calls a card shows
DETAIL_CHARS = 80              # a command or file as a card shows it
INITIAL_BYTES = 32 * 1024 * 1024  # a first read of a larger file starts this far from its end
TAIL_TTL = 1800.0              # a tail nobody asked about for this long is dropped

# Commands that build, and commands that test, by the words they start with or contain.
_BUILD = re.compile(r'(?:^|[\s;&|/])(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?build\b|\bnext\s+build\b|\bvite\s+build\b'
                    r'|\bastro\s+build\b|\btsc\b|\bgo\s+build\b|\bcargo\s+build\b|(?:^|[\s;&|])make\b|\bgradle\w*\b'
                    r'|\bmvn\b|\bwebpack\b|\bdocker\s+build\b|\bpyinstaller\b|\bpython\S*\s+-m\s+build\b')
_TEST = re.compile(r'\b(?:pytest|unittest|playwright|jest|vitest|mocha|tox|nox)\b|\bgo\s+test\b|\bcargo\s+test\b'
                   r'|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b|\bnpx\s+playwright\b|\bdotnet\s+test\b')
_PATCH_FILE = re.compile(r'\*\*\* (?:Update|Add|Delete) File: (.+)')
# Codex's code-mode exec calls its tools from code: the shell command is a
# string or list after command: or cmd:.
_CODE_COMMAND = re.compile(r"""\b(?:command|cmd)\s*:\s*(\[[^\]]*\]|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`[^`]*`)""")
_CODE_STRING = re.compile(r""""((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`([^`]*)`""")
_INTERRUPTED = '[Request interrupted'

_IMPRINT_CALL = re.compile(r'mcp__imprint__(\w+)')
_SEARCH_TOOLS = {'search', 'recall', 'get_memory', 'neighbors', 'file_chunks', 'file_summary', 'read_source',
                 'list_sources', 'kg_query', 'graph_scope', 'status', 'device_context', 'skills'}
_BOARD_TOOLS = {'tasks', 'task_edit'}
_SAVE_TOOLS = {'store', 'ingest_url', 'ingest_content', 'kg_edit', 'skill_edit', 'delete', 'purge_sources'}
# Claude Code's own tools that are neither code nor shell, by what they do.
_NAMED_TOOLS = {'TodoWrite': 'Planning', 'SendMessage': 'Messaging a session', 'ToolSearch': 'Loading tools',
                'Skill': 'Loading a skill', 'ScheduleWakeup': 'Scheduling', 'Monitor': 'Watching a command'}
_EDIT_TOOLS = {'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'apply_patch'}
_READ_TOOLS = {'Read', 'Grep', 'Glob', 'LS', 'NotebookRead'}
_WEB_TOOLS = {'WebFetch', 'WebSearch', 'web_search', 'web_fetch'}
_DELEGATE_TOOLS = {'Task', 'Agent'}
_ASK_TOOLS = {'AskUserQuestion', 'ExitPlanMode', 'request_user_input'}
_SHELL_TOOLS = {'Bash', 'shell', 'exec_command', 'local_shell_call', 'container.exec', 'unified_exec'}
# Tools a client usually asks the person to approve: a call to one without a
# result for a while may be waiting in its terminal (ADR 0246).
APPROVAL_TOOLS = _SHELL_TOOLS | {'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'apply_patch', 'WebFetch', 'web_fetch'}
QUESTION_CHARS = 300           # a question as Live and Needs you quote it
OPTIONS = 6                    # a choice question's options shown


def _short(text: str, limit: int = DETAIL_CHARS) -> str:
    text = ' '.join(str(text or '').split())
    text = redact(text)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _command(value) -> str:
    if isinstance(value, list):
        value = ' '.join(str(part) for part in value)
        value = re.sub(r'^(?:bash|sh|zsh)\s+-l?c\s+', '', value)
    return str(value or '')


def _command_kind(command: str) -> tuple[str, str]:
    """Building, Testing or Running, from the command's first line: a heredoc's
    body (a script the command feeds) is not what it runs."""
    command = command.strip().splitlines()[0] if command.strip() else ''
    if _TEST.search(command):
        return 'testing', 'Testing'
    if _BUILD.search(command):
        return 'building', 'Building'
    return 'running', 'Running'


def classify(tool: str, payload) -> dict:
    """``{kind, label, detail}`` for one tool call, by rule.

    ``kind`` is one of editing, building, testing, running, reading, web,
    imprint, board, delegating, asking or other; ``detail`` is a shortened,
    redacted command, file or tool name."""
    tool = tool or ''
    data = payload if isinstance(payload, dict) else {}
    imprint = _IMPRINT_CALL.search(tool) or (_IMPRINT_CALL.search(payload) if isinstance(payload, str) else None)
    if imprint:
        name = imprint.group(1)
        if name in _BOARD_TOOLS:
            return {'kind': 'board', 'label': 'Updating the Board', 'detail': name}
        if name in _SEARCH_TOOLS:
            return {'kind': 'imprint', 'label': 'Searching Imprint', 'detail': name}
        if name in _SAVE_TOOLS:
            return {'kind': 'imprint', 'label': 'Saving to Imprint', 'detail': name}
        return {'kind': 'imprint', 'label': 'Using Imprint', 'detail': name}
    if tool in _EDIT_TOOLS or (isinstance(payload, str) and '*** Begin Patch' in payload):
        path = data.get('file_path') or data.get('notebook_path') or data.get('path') or ''
        if not path and isinstance(payload, str):
            found = _PATCH_FILE.search(payload)
            path = found.group(1) if found else ''
        name = Path(str(path).replace('\\', '/')).name if path else ''
        return {'kind': 'editing', 'label': f'Editing {_short(name, 48)}' if name else 'Editing', 'detail': _short(path)}
    if tool in _READ_TOOLS:
        target = data.get('file_path') or data.get('pattern') or data.get('path') or ''
        return {'kind': 'reading', 'label': 'Reading code', 'detail': _short(target)}
    if tool in _WEB_TOOLS:
        return {'kind': 'web', 'label': 'Searching the web', 'detail': _short(data.get('url') or data.get('query') or '')}
    if tool in _DELEGATE_TOOLS:
        return {'kind': 'delegating', 'label': 'Delegating', 'detail': _short(data.get('description') or data.get('subagent_type') or '')}
    if tool in _ASK_TOOLS:
        return {'kind': 'asking', 'label': 'Asking you', 'detail': ''}
    if tool in _NAMED_TOOLS:
        return {'kind': 'other', 'label': _NAMED_TOOLS[tool], 'detail': ''}
    if tool == 'exec' and isinstance(payload, str):
        found = _CODE_COMMAND.search(payload)
        if not found:
            if 'write_stdin' in payload:
                return {'kind': 'running', 'label': 'Running', 'detail': 'reading a running command'}
            return {'kind': 'running', 'label': 'Running code', 'detail': _short(payload.splitlines()[0] if payload else '')}
        command = _command([a or b or c for a, b, c in _CODE_STRING.findall(found.group(1))])
        kind, label = _command_kind(command)
        return {'kind': kind, 'label': label, 'detail': _short(command)}
    if tool in _SHELL_TOOLS or tool == 'exec':
        command = _command(data.get('command') or data.get('cmd') or (payload if isinstance(payload, str) else ''))
        kind, label = _command_kind(command)
        return {'kind': kind, 'label': label, 'detail': _short(command)}
    if tool.startswith('mcp__'):
        return {'kind': 'other', 'label': f'Using {tool.split("__")[1]}', 'detail': _short(tool.split('__')[-1])}
    return {'kind': 'other', 'label': f'Using {_short(tool, 32)}' if tool else 'Working', 'detail': ''}


def _timestamp(record: dict) -> float | None:
    value = record.get('timestamp')
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _day(at: float | None) -> str:
    return time.strftime('%Y-%m-%d', time.localtime(at if at is not None else time.time()))


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def asked(tool: str, payload) -> dict:
    """What an asking tool asks the person (ADR 0246): ``{question,
    options}``, redacted and shortened. Claude Code's AskUserQuestion takes
    ``questions``, each with ``options`` labelled; ExitPlanMode asks to
    approve a plan; Codex's request_user_input names a prompt."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {'question': payload}
    payload = payload if isinstance(payload, dict) else {}
    question, options = None, []
    questions = payload.get('questions')
    if isinstance(questions, list) and questions and isinstance(questions[0], dict):
        first = questions[0]
        question = first.get('question') or first.get('header')
        options = [o.get('label') if isinstance(o, dict) else str(o) for o in first.get('options') or []]
        if len(questions) > 1 and question:
            question = f'{question} (and {len(questions) - 1} more)'
    elif tool == 'ExitPlanMode':
        question = 'Approve its plan?'
    else:
        question = next((payload[k] for k in ('question', 'prompt', 'message', 'text') if isinstance(payload.get(k), str)), None)
    return {'question': _short(redact(str(question)), QUESTION_CHARS) if question else None,
            'options': [_short(redact(str(o)), DETAIL_CHARS) for o in options if o][:OPTIONS]}


_TRAILING = re.compile(r'[\s*_`"\')\]]+$')


def ending_question(text: str | None) -> str | None:
    """The question a message ends with, quoted as its last sentence, or
    None when it does not end by asking something (ADR 0246)."""
    if not text:
        return None
    trimmed = _TRAILING.sub('', text)
    if not trimmed.endswith('?'):
        return None
    line = trimmed.splitlines()[-1].strip().lstrip('-*#> ').strip()
    starts = [m.end() for m in re.finditer(r'[.!?](?:\s+|$)', line[:-1])]
    sentence = line[starts[-1]:] if starts else line
    sentence = sentence.strip().strip('*_`').strip()
    return _short(redact(sentence), QUESTION_CHARS) if sentence else None


class State:
    """What one transcript says so far."""

    def __init__(self, client: str):
        self.client = client
        self.model: str | None = None
        self.window: int | None = None
        self.rate_limit: dict | None = None
        self.context = 0
        self.usage: dict[str, tuple[int, int, int, int, int, str]] = {}  # Claude Code: message id → usage, day
        self.totals = {'new_input': 0, 'cached_input': 0, 'output': 0, 'thinking': 0}  # Codex: running totals
        self.days: dict[str, dict[str, int]] = {}  # Codex: tokens per local day
        self.pending: dict[str, dict] = {}  # tool calls without a result, by call id
        self.done: deque[dict] = deque(maxlen=ACTIONS)
        self.turn_running = False
        self.turn_changed: float | None = None  # when the latest turn started or ended
        self.last_text: str | None = None  # the agent's last message of the turn, to see whether it asked something
        self.last_at: float | None = None
        self.partial = False  # a first read began past the start of a large file

    # ── Claude Code ──────────────────────────────────────────────────────

    def _claude(self, record: dict, at: float | None) -> None:
        kind = record.get('type')
        message = record.get('message') if isinstance(record.get('message'), dict) else {}
        if kind == 'assistant':
            self.model = message.get('model') or self.model
            usage = message.get('usage') if isinstance(message.get('usage'), dict) else None
            if usage and message.get('id'):
                details = usage.get('output_tokens_details') if isinstance(usage.get('output_tokens_details'), dict) else {}
                first_day = self.usage.get(message['id'], (0, 0, 0, 0, 0, _day(at)))[5]
                self.usage[message['id']] = (_int(usage.get('input_tokens')), _int(usage.get('cache_creation_input_tokens')),
                                             _int(usage.get('cache_read_input_tokens')), _int(usage.get('output_tokens')),
                                             _int(details.get('thinking_tokens')), first_day)
                self.context = (_int(usage.get('input_tokens')) + _int(usage.get('cache_creation_input_tokens'))
                                + _int(usage.get('cache_read_input_tokens')))
            for block in message.get('content') or []:
                if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('id'):
                    self._call(block['id'], block.get('name') or '', block.get('input'), at)
                elif isinstance(block, dict) and block.get('type') == 'text' and str(block.get('text') or '').strip():
                    self.last_text = str(block['text'])
            if message.get('stop_reason') in ('end_turn', 'stop_sequence', 'max_tokens', 'refusal') and not self.pending:
                self._turn(False, at)
            elif not self.turn_running:
                self._turn(True, at)
        elif kind == 'user':
            content = message.get('content')
            results = [b for b in content if isinstance(b, dict) and b.get('type') == 'tool_result'] if isinstance(content, list) else []
            for block in results:
                self._finish(block.get('tool_use_id'), at)
            if not results and not record.get('isMeta') and not record.get('isCompactSummary') and content:
                self.pending.clear()  # a new prompt: anything unanswered was interrupted
                self.last_text = None  # and whatever it asked was answered
                text = content if isinstance(content, str) else ' '.join(
                    b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
                self._turn(not text.lstrip().startswith(_INTERRUPTED), at)

    # ── Codex ────────────────────────────────────────────────────────────

    def _codex(self, record: dict, at: float | None) -> None:
        payload = record.get('payload') if isinstance(record.get('payload'), dict) else {}
        kind, sub = record.get('type'), payload.get('type')
        if kind == 'turn_context':
            self.model = payload.get('model') or self.model
        elif sub == 'task_started':
            self.last_text = None
            self._turn(True, at)
        elif sub == 'agent_message' and isinstance(payload.get('message'), str) and payload['message'].strip():
            self.last_text = payload['message']
        elif kind == 'response_item' and sub == 'message' and payload.get('role') == 'assistant':
            text = ' '.join(b.get('text', '') for b in payload.get('content') or []
                            if isinstance(b, dict) and b.get('type') in ('output_text', 'text'))
            if text.strip():
                self.last_text = text
        elif sub in ('task_complete', 'turn_aborted'):
            self.pending.clear()
            self._turn(False, at)
        elif sub == 'token_count':
            info = payload.get('info') if isinstance(payload.get('info'), dict) else {}
            total = info.get('total_token_usage') if isinstance(info.get('total_token_usage'), dict) else None
            last = info.get('last_token_usage') if isinstance(info.get('last_token_usage'), dict) else {}
            if total:
                cached, given = _int(total.get('cached_input_tokens')), _int(total.get('input_tokens'))
                now = {'new_input': max(0, given - cached), 'cached_input': cached,
                       'output': _int(total.get('output_tokens')), 'thinking': _int(total.get('reasoning_output_tokens'))}
                day = self.days.setdefault(_day(at), {'new_input': 0, 'cached_input': 0, 'output': 0})
                for key in day:
                    day[key] += max(0, now[key] - self.totals[key])
                self.totals = now
            self.context = _int(last.get('input_tokens')) or self.context
            self.window = _int(info.get('model_context_window')) or self.window
            limits = payload.get('rate_limits') if isinstance(payload.get('rate_limits'), dict) else {}
            primary = limits.get('primary') if isinstance(limits.get('primary'), dict) else None
            if primary and isinstance(primary.get('used_percent'), (int, float)):
                self.rate_limit = {'used_percent': float(primary['used_percent']),
                                   'window_minutes': primary.get('window_minutes'), 'resets_at': primary.get('resets_at')}
        elif sub in ('function_call', 'custom_tool_call', 'local_shell_call') and payload.get('call_id'):
            body = payload.get('arguments') if sub == 'function_call' else payload.get('input')
            if sub == 'local_shell_call':
                body = (payload.get('action') or {})
            if isinstance(body, str) and sub == 'function_call':
                try:
                    body = json.loads(body)
                except ValueError:
                    pass
            self._call(payload['call_id'], payload.get('name') or sub, body, at)
            if not self.turn_running:
                self._turn(True, at)
        elif sub in ('function_call_output', 'custom_tool_call_output', 'local_shell_call_output'):
            self._finish(payload.get('call_id'), at)

    # ── Shared ───────────────────────────────────────────────────────────

    def _call(self, call_id: str, tool: str, payload, at: float | None) -> None:
        """A tool call in flight: its activity, its tool, and what an asking tool asks."""
        action = {**classify(tool, payload), 'tool': tool, 'at': at}
        if action['kind'] == 'asking':
            action.update(asked(tool, payload))
        self.pending[call_id] = action

    def _turn(self, running: bool, at: float | None) -> None:
        if running != self.turn_running or self.turn_changed is None:
            self.turn_running, self.turn_changed = running, at

    def _finish(self, call_id, at: float | None) -> None:
        action = self.pending.pop(call_id, None) if call_id else None
        if action:
            self.done.append({**action, 'finished_at': at})

    def feed(self, record: dict) -> None:
        if not isinstance(record, dict):
            return
        at = _timestamp(record)
        if at is not None:
            self.last_at = max(self.last_at or at, at)
        if self.client == 'codex':
            self._codex(record, at)
        else:
            self._claude(record, at)

    def tokens(self) -> dict:
        """Context in use, the model's window where known, and this session's tokens."""
        if self.client == 'codex':
            totals = dict(self.totals)
        else:
            totals = {'new_input': sum(u[0] + u[1] for u in self.usage.values()),
                      'cached_input': sum(u[2] for u in self.usage.values()),
                      'output': sum(u[3] for u in self.usage.values()),
                      'thinking': sum(u[4] for u in self.usage.values())}
        given = totals['new_input'] + totals['cached_input']
        return {**totals, 'context': self.context, 'window': self.window,
                'cache_share': round(totals['cached_input'] / given, 3) if given else None,
                'rate_limit': self.rate_limit, 'partial': self.partial}

    def day_tokens(self, day: str) -> dict[str, int]:
        """Tokens of one local day, for the header's totals."""
        if self.client == 'codex':
            return dict(self.days.get(day, {'new_input': 0, 'cached_input': 0, 'output': 0}))
        found = [u for u in self.usage.values() if u[5] == day]
        return {'new_input': sum(u[0] + u[1] for u in found), 'cached_input': sum(u[2] for u in found),
                'output': sum(u[3] for u in found)}

    def actions(self, now: float) -> list[dict]:
        """The calls in flight, newest first, then the last finished ones: at most :data:`ACTIONS`."""
        running = sorted(self.pending.values(), key=lambda a: a.get('at') or 0, reverse=True)
        shown = [{**a, 'running': True} for a in running] + [{**a, 'running': False} for a in reversed(self.done)]
        return [{key: a.get(key) for key in ('kind', 'label', 'detail', 'at', 'running')} for a in shown[:ACTIONS]]


class Tail:
    """One transcript, read a piece at a time."""

    def __init__(self, path: str, client: str):
        self.path, self.client = path, client
        self.offset = 0
        self.state = State(client)
        self.asked = time.monotonic()

    def read(self) -> State:
        """Fold in the records added since the last read; restart when the file shrank."""
        self.asked = time.monotonic()
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return self.state
        if size < self.offset:
            self.offset, self.state = 0, State(self.client)
        if size == self.offset:
            return self.state
        with open(self.path, 'rb') as handle:
            if self.offset == 0 and size > INITIAL_BYTES:
                handle.seek(size - INITIAL_BYTES)
                handle.readline()  # skip the line the seek landed in
                self.state.partial = True
                self.offset = handle.tell()
            else:
                handle.seek(self.offset)
            data = handle.read(size - self.offset)
        end = data.rfind(b'\n')
        if end < 0:
            return self.state  # a line still being written
        for line in data[:end].splitlines():
            try:
                self.state.feed(json.loads(line))
            except ValueError:
                continue
        self.offset += end + 1
        return self.state


def summarize(path: str, client: str) -> State | None:
    """A whole transcript folded at once, for a finished session; not kept.
    None when it cannot be read."""
    state = State(client)
    try:
        with open(path, 'rb') as handle:
            for line in handle:
                try:
                    state.feed(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return None
    return state


_tails: dict[str, Tail] = {}
_lock = threading.Lock()


def read(path: str, client: str) -> State:
    """The state of one transcript, read up to now. Tails nobody asked about
    for :data:`TAIL_TTL` are dropped."""
    key = f'{client}|{path}'
    with _lock:
        now = time.monotonic()
        for stale in [k for k, tail in _tails.items() if now - tail.asked > TAIL_TTL]:
            del _tails[stale]
        tail = _tails.get(key) or _tails.setdefault(key, Tail(path, client))
        return tail.read()


def forget() -> None:
    """Drop every tail (tests)."""
    with _lock:
        _tails.clear()
