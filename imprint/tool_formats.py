"""Tool-call formats of local chat models (ADR 0061).

A local model is shown its tools by its own chat template, which renders the
JSON schemas, earlier calls and tool results. What differs between model
families is the grammar of the reply: how a call is written, and which marker
opens it. Each format here parses exactly its family's documented shape.

A format is detected from the template itself: the template renders a probe
call the way its model was trained to write calls, and the format whose
parser reads that rendering back is the template's format.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, NamedTuple


class Format(NamedTuple):
    name: str
    label: str
    detail: str
    markers: tuple[str, ...]  # text that opens a call
    at_start: bool            # a call is the whole reply, so it can only open at its start
    # reply text -> [{"name", "args"}], with arguments as written; the chat
    # types them by each tool's schema
    parse: Callable[[str], list[dict]]


def _call(obj: Any, args_key: str, optional: dict[str, Any] | None = None) -> dict | None:
    """A call written as a JSON object holding exactly "name" and args_key,
    plus any optional keys, each with its one allowed value."""
    optional = optional or {}
    if not isinstance(obj, dict) or not {"name", args_key} <= set(obj) <= {"name", args_key, *optional}:
        return None
    if not isinstance(obj["name"], str) or not isinstance(obj[args_key], dict):
        return None
    if any(key in obj and value is not None and obj[key] != value for key, value in optional.items()):
        return None
    return {"name": obj["name"], "args": obj[args_key]}


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


# Qwen3.5 and Qwen3-Coder:
#   <tool_call>\n<function=NAME>\n<parameter=P>\nvalue\n</parameter>\n</function>\n</tool_call>
_XML_CALL = re.compile(r"<tool_call>\s*<function=([^>\n]+)>(.*?)</function>\s*</tool_call>", re.DOTALL)
_XML_PARAM = re.compile(r"<parameter=([^>\n]+)>\n?(.*?)\n?</parameter>", re.DOTALL)


def _parse_qwen_xml(text: str) -> list[dict]:
    """Parameter values are text, taken verbatim, multi-line included."""
    return [{"name": match.group(1).strip(),
             "args": {key.strip(): value for key, value in _XML_PARAM.findall(match.group(2))}}
            for match in _XML_CALL.finditer(text)]


# Hermes, Qwen2.5 and Qwen3: <tool_call>\n{"name": NAME, "arguments": {...}}\n</tool_call>
_HERMES_CALL = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


def _parse_hermes(text: str) -> list[dict]:
    calls = [_call(_json(body), "arguments") for body in _HERMES_CALL.findall(text)]
    return [call for call in calls if call]


# Llama 3.1-3.3: the whole reply is {"name": NAME, "parameters": {...}},
# optionally after <|python_tag|>, the token Meta's format marks tool calls
# with. The models also write "type": "function" first, echoing how the
# template lists the tools; llama.cpp's Llama 3.x parser accepts it too.
def _parse_llama3(text: str) -> list[dict]:
    call = _call(_json(text.strip().removeprefix("<|python_tag|>")), "parameters", {"type": "function"})
    return [call] if call else []


# Mistral (v3 and Tekken tokenizers): [TOOL_CALLS][{"name": NAME, "arguments": {...}, "id": ...}, ...]
def _parse_mistral(text: str) -> list[dict]:
    start = text.find("[TOOL_CALLS]")
    items = _json(text[start + len("[TOOL_CALLS]"):]) if start >= 0 else None
    if not isinstance(items, list) or not items:
        return []
    calls = [_call(item, "arguments", {"id": None}) for item in items]
    return calls if all(calls) else []


FORMATS: dict[str, Format] = {f.name: f for f in (
    Format("qwen-xml", "Qwen XML", "Qwen3.5, Qwen3-Coder: <tool_call><function=…><parameter=…>",
           ("<tool_call>",), False, _parse_qwen_xml),
    Format("hermes", "Hermes JSON", "Qwen2.5, Qwen3, Hermes: <tool_call>{\"name\", \"arguments\"}</tool_call>",
           ("<tool_call>",), False, _parse_hermes),
    Format("llama3", "Llama 3", "Llama 3.1–3.3: a reply of {\"name\", \"parameters\"}",
           ("{", "<|python_tag|>"), True, _parse_llama3),
    Format("mistral", "Mistral", "Mistral, Mistral Nemo: [TOOL_CALLS][{\"name\", \"arguments\"}]",
           ("[TOOL_CALLS]",), False, _parse_mistral),
)}


def _partial(text: str, marker: str) -> int:
    """Length of a trailing prefix of marker that may still complete it."""
    for n in range(min(len(marker) - 1, len(text)), 0, -1):
        if marker.startswith(text[-n:]):
            return n
    return 0


def shown(fmt: Format, text: str) -> int:
    """How much of a reply streamed so far is reply text: everything before a
    call marker, holding back a tail that may still become one."""
    if fmt.at_start:
        head = text.lstrip()
        return 0 if any(m.startswith(head) or head.startswith(m) for m in fmt.markers) else len(text)
    starts = [i for i in (text.find(m) for m in fmt.markers) if i >= 0]
    if starts:
        return min(starts)
    return len(text) - max(_partial(text, m) for m in fmt.markers)


_PROBE_TOOL = {"type": "function", "function": {
    "name": "probe", "description": "Probe",
    "parameters": {"type": "object", "properties": {"value": {"type": "string", "description": "Probe"}},
                   "required": ["value"]}}}
# A call id every supported template accepts: Mistral's require 9 alphanumerics.
_PROBE_CALL = {"id": "probe0000", "type": "function", "function": {"name": "probe", "arguments": {"value": "probe"}}}


def detect(render: Callable[[list[dict], list[dict]], str], eos_token: str) -> Format | None:
    """The format of a chat template. `render(messages, tools)` renders a
    prompt through it. The template writes a probe call as its model was
    trained to; the format whose parser reads that back is the template's.
    None when the template renders no tool call any format reads."""
    question = [{"role": "user", "content": "probe"}]
    try:
        before = render(question, [_PROBE_TOOL])
        after = render(question + [{"role": "assistant", "content": "", "tool_calls": [_PROBE_CALL]}], [_PROBE_TOOL])
    except Exception:
        return None  # e.g. a template that cannot render assistant tool calls
    if not after.startswith(before):
        return None
    written = after[len(before):]
    if eos_token:
        written = written.split(eos_token)[0]
    expected = [{"name": "probe", "args": {"value": "probe"}}]
    return next((f for f in FORMATS.values() if f.parse(written) == expected), None)
