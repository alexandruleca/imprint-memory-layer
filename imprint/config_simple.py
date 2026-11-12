"""The simple dashboard's settings (ADR 0182): a short list of choices in
plain words over the same settings the advanced dashboard edits.

Every item names one or more keys of ``config_schema.SETTINGS``. A switch is
one boolean key. A choice is a list of options, each a value for every key of
the item: the values of one key under plainer names, or a preset over
several. Values that match no option show as "Custom": they were set in the
advanced dashboard, and the simple one leaves them alone until the user picks
an option. Nothing here stores anything of its own.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .config_schema import SETTINGS_BY_KEY, resolve, set_value


@dataclass(frozen=True)
class Option:
    label: str
    detail: str
    values: dict[str, Any]
    # An environment variable the option needs; it is unavailable without it.
    needs_env: str = ""


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    desc: str
    key: str = ""  # a switch: one boolean setting
    options: tuple[Option, ...] = ()  # a choice
    own_choices: str = ""  # a choice whose options are this setting's own choices
    link: str = ""  # a page of the dashboard instead of a setting
    link_label: str = ""
    kind: str = ""  # "theme" and "report": drawn by the dashboard or computed here
    when: tuple[tuple[str, Any], ...] = ()  # shown while these settings have these values
    desktop: bool = False  # only the desktop app uses it


@dataclass(frozen=True)
class Group:
    id: str
    title: str
    desc: str
    items: tuple[Item, ...]
    advanced: tuple[tuple[str, str], ...] = ()  # (advanced section id, its title)


_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GOOGLE_API_KEY"}
_COMPANY = {"anthropic": "Anthropic", "openai": "OpenAI", "gemini": "Google", "ollama": "your Ollama server",
            "vllm": "your vLLM server"}

GROUPS: tuple[Group, ...] = (
    Group("assistant", "Assistant", "The model that answers in Chat and when you talk to Echo.", (
        Item("assistant-runs", "Where the assistant runs",
             "On this computer, nothing you ask leaves it. With a company's model, your questions and the parts of "
             "your memory used to answer them are sent to that company.",
             options=(
                 Option("On this computer", "Private. Downloads its model the first time you chat.", {"chat.provider": "local"}),
                 Option("Anthropic (Claude)", "Needs an Anthropic API key on this computer.", {"chat.provider": "anthropic"},
                        needs_env=_KEY_ENV["anthropic"]),
                 Option("OpenAI", "Needs an OpenAI API key on this computer.", {"chat.provider": "openai"},
                        needs_env=_KEY_ENV["openai"]),
                 Option("Google Gemini", "Needs a Google API key on this computer.", {"chat.provider": "gemini"},
                        needs_env=_KEY_ENV["gemini"]),
             )),
        Item("assistant-model", "Model on this computer",
             "The larger model answers better and needs a computer with a lot of memory.",
             own_choices="chat.local_model", when=(("chat.provider", "local"),)),
        Item("assistant-depth", "How far it looks before answering",
             "A deeper answer reads more of your memory first and takes longer.",
             options=(
                 Option("Quick", "Up to 2 minutes of looking things up", {"chat.research_timeout_sec": 120, "chat.research_token_budget": 32768}),
                 Option("Balanced", "Up to 5 minutes", {"chat.research_timeout_sec": 300, "chat.research_token_budget": 65536}),
                 Option("Thorough", "Up to 15 minutes, for questions that span a lot of work",
                        {"chat.research_timeout_sec": 900, "chat.research_token_budget": 196608}),
             )),
    ), advanced=(("settings-chat", "Chat"),)),
    Group("echo", "Echo", "The voice companion you talk to.", (
        Item("echo-look", "Look and voice", "How Echo looks and sounds.", own_choices="voice.avatar"),
        Item("echo-save", "Keep conversations in Chat",
             "What you and Echo said is saved as text in Chat on this computer. The sound is never saved.",
             key="voice.save_conversations"),
        Item("echo-wait", "How long Echo waits after “Hey Echo”",
             "After answering, Echo listens this long for a follow-up before it goes away.",
             options=(
                 Option("10 seconds", "", {"voice.wake.idle_seconds": 10}),
                 Option("15 seconds", "", {"voice.wake.idle_seconds": 15}),
                 Option("30 seconds", "If you often pause to think", {"voice.wake.idle_seconds": 30}),
                 Option("1 minute", "", {"voice.wake.idle_seconds": 60}),
             ), desktop=True),
        Item("echo-devices", "Microphone, speaker and “Hey Echo”",
             "Chosen on Echo's own page, under Companion settings.", link="/voice/", link_label="Open Echo"),
    ), advanced=(("settings-voice", "Voice"),)),
    Group("recordings", "Recordings", "Meetings and calls you record, and audio files you add.", (
        Item("recordings-quality", "Transcription quality",
             "How carefully speech is turned into text. All of it happens on this computer.",
             options=(
                 Option("Most accurate", "Slowest. A 2.9 GB download the first time.", {"audio.model": "large-v3"}),
                 Option("Faster", "About twice as fast, a little less accurate. 1.5 GB.", {"audio.model": "large-v3-turbo"}),
                 Option("Fastest", "For quick drafts; more mistakes. 0.5 GB.", {"audio.model": "small"}),
             )),
        Item("recordings-language", "Language spoken", "Automatic works out the language as it listens.",
             own_choices="audio.language"),
        Item("recordings-voices", "Tell voices apart",
             "The transcript says who spoke: you, others in the room, and each person on the call.",
             key="audio.diarize"),
    ), advanced=(("settings-audio", "Audio ingestion"),)),
    Group("tidy", "Keeping memory tidy", "Work Imprint does by itself while nothing else is running.", (
        Item("tidy-sort", "Sort new memories into projects",
             "Imprint works out which project a conversation or file belongs to and suggests where to put it.",
             key="organize.background"),
        Item("tidy-move", "Move the obvious ones without asking",
             "When a conversation plainly belongs to one project, it is moved there and you can move it back. "
             "The rest wait for you.", key="organize.auto_move"),
        Item("tidy-labels", "Smarter labels",
             "A model reads each new memory and labels what it is about, which makes it easier to find. "
             "It runs in the background and takes a while on a large memory.", key="tagger.llm"),
        Item("tidy-recaps", "A recap after each AI session",
             "When a session with an AI tool ends, a model writes a short recap and keeps its decisions as memories.",
             key="summarizer.enabled"),
    ), advanced=(("settings-organize", "Organize"), ("settings-tagger", "Tagging"), ("settings-summarizer", "Session summaries"))),
    Group("privacy", "Privacy", "What Imprint stores, and what leaves this computer.", (
        Item("privacy-leaves", "What leaves this computer", "", kind="report"),
        Item("privacy-secrets", "Extra check for secrets",
             "Passwords and keys in known formats are always removed before anything is stored. This adds a model "
             "that also finds less obvious ones and account numbers. A 945 MB download the first time.",
             key="redact.privacy_filter"),
    ), advanced=(("settings-redact", "Redaction"),)),
    Group("devices", "Devices", "Your other computers that share this memory.", (
        Item("devices-pair", "Pair another device", "Connect two computers so both hold the same memory.",
             link="/sync/", link_label="Open Devices"),
        Item("devices-recordings", "Share recordings between my devices",
             "Recordings hold other people's voices, so they travel only between devices where you turned this on, "
             "on both.", key="sync.recordings"),
    ), advanced=(("settings-sync", "Device sync"),)),
    Group("app", "App", "How the dashboard and the desktop app behave.", (
        Item("app-theme", "Appearance", "Light, dark, or the same as this computer.", kind="theme"),
        Item("app-mode", "Dashboard", "Simple shows the everyday pages. Advanced shows every page and every setting.",
             own_choices="ui.mode"),
        Item("app-tutorials", "Tutorials", "A page's short tutorial starts the first time you open it.",
             key="ui.tutorials"),
        Item("app-orb", "Floating button",
             "A small Imprint button stays on screen while the dashboard is hidden, for Echo and for starting a recording.",
             key="desktop.floating_orb", desktop=True),
        Item("app-workspaces", "Separate memories",
             "Keep work and personal memory apart: each workspace holds its own memories.",
             link="#workspaces", link_label="Manage workspaces"),
        Item("app-updates", "Install updates by itself",
             "New versions are installed when nothing would be interrupted.", key="update.auto"),
    ), advanced=(("settings-ui", "Dashboard"), ("settings-desktop", "Desktop app"), ("settings-update", "Updates"))),
)

ITEMS: dict[str, Item] = {item.id: item for group in GROUPS for item in group.items}


def _options(item: Item) -> tuple[Option, ...]:
    if not item.own_choices:
        return item.options
    setting = SETTINGS_BY_KEY[item.own_choices]
    return tuple(Option(choice.label, choice.unavailable or choice.detail, {setting.key: choice.value})
                 for choice in setting.options())


def keys(item: Item) -> tuple[str, ...]:
    """The settings an item reads and writes."""
    if item.key:
        return (item.key,)
    found: list[str] = []
    for option in _options(item):
        found.extend(key for key in option.values if key not in found)
    return tuple(found)


def _unavailable(item: Item, option: Option) -> str:
    if option.needs_env and not os.environ.get(option.needs_env):
        return f"No key found on this computer ({option.needs_env})"
    if item.own_choices:
        setting = SETTINGS_BY_KEY[item.own_choices]
        value = option.values[setting.key]
        return next((choice.unavailable for choice in setting.options() if choice.value == value), "")
    return ""


def _leaves() -> list[str]:
    """In plain words, what is sent off this computer with the settings as they are."""
    lines = []
    provider = resolve("chat.provider")[0]
    if provider != "local":
        lines.append(f"Chat and Echo: your questions, and the parts of your memory used to answer them, go to "
                     f"{_COMPANY.get(provider, provider)}.")
    if resolve("tagger.llm")[0]:
        provider = resolve("tagger.llm_provider")[0]
        if provider != "local":
            lines.append(f"Smarter labels: each new memory's text goes to {_COMPANY.get(provider, provider)}.")
    if resolve("summarizer.enabled")[0]:
        provider = resolve("summarizer.provider")[0]
        if provider != "local":
            lines.append(f"Session recaps: each session's conversation goes to {_COMPANY.get(provider, provider)}.")
    return lines


def _item(item: Item) -> dict:
    row: dict[str, Any] = {"id": item.id, "title": item.title, "desc": item.desc, "desktop": item.desktop}
    if item.link:
        return {**row, "kind": "link", "href": item.link, "label": item.link_label}
    if item.kind == "theme":
        return {**row, "kind": "theme"}
    if item.kind == "report":
        lines = _leaves()
        return {**row, "kind": "report", "ok": not lines,
                "lines": lines or ["Nothing. Chat, labels and recaps all run on this computer."],
                "note": "Web pages you add are fetched from their sites, and paired devices exchange your memory "
                        "with each other."}
    resolved = {key: resolve(key) for key in keys(item)}
    # A value an environment variable sets cannot be changed from the dashboard.
    locked = next((SETTINGS_BY_KEY[key].env for key, (_, source) in resolved.items() if source == "env"), "")
    row["locked"] = locked
    row["keys"] = list(resolved)
    if item.key:
        return {**row, "kind": "switch", "on": bool(resolved[item.key][0])}
    options = _options(item)
    selected = next((index for index, option in enumerate(options)
                     if all(resolved[key][0] == value for key, value in option.values.items())), None)
    return {**row, "kind": "choice", "selected": selected,
            "options": [{"label": option.label, "detail": option.detail, "unavailable": _unavailable(item, option)}
                        for option in options]}


def _visible(item: Item) -> bool:
    return all(resolve(key)[0] == value for key, value in item.when)


def catalog() -> list[dict]:
    """The groups with each item's current state, for the simple settings page."""
    return [{"id": group.id, "title": group.title, "desc": group.desc,
             "advanced": [{"id": section, "title": title} for section, title in group.advanced],
             "items": [_item(item) for item in group.items if _visible(item)]}
            for group in GROUPS]


def apply(item_id: str, *, on: bool | None = None, option: int | None = None) -> list[str]:
    """Write an item's choice; returns the keys written. Raises KeyError for
    an unknown item and ValueError for a choice it does not offer."""
    item = ITEMS.get(item_id)
    if item is None or item.link or item.kind:
        raise KeyError(f"unknown setting: {item_id}")
    if item.key:
        if on is None:
            raise ValueError("expected on: true or false")
        set_value(item.key, "true" if on else "false")
        return [item.key]
    options = _options(item)
    if option is None or isinstance(option, bool) or not 0 <= option < len(options):
        raise ValueError(f"expected an option from 0 to {len(options) - 1}")
    chosen = options[option]
    reason = _unavailable(item, chosen)
    if reason:
        raise ValueError(reason)
    for key, value in chosen.values.items():
        set_value(key, str(value))
    return list(chosen.values)
