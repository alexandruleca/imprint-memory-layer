"""Reminders, timers and scheduled jobs (ADR 0158).

The user asks for them by chat, by voice (Echo) or on the dashboard's
Reminders page. They live in ``data/schedule.sqlite3``: device-local, never
synced, outside every workspace. Four kinds:

- ``reminder``: a message at a time.
- ``timer``: a duration, shown counting down; it ends with an alert.
- ``job``: a queue command from an allow-list (``JOBS``), submitted through
  ``queue.submit`` like any dashboard job (ADRs 0064, 0068). Never a shell.
- ``prompt``: a question for Imprint; the answer is a normal chat reply in a
  new chat (``chat_replies``), and the alert links to it.

A scheduler thread in the API process sleeps until the next due entry (a
change wakes it), fires it and moves a recurring entry to its next
occurrence. A firing is committed before anything acts on it, and an
occurrence can fire only once (``UNIQUE(entry_id, occurrence_at)`` and a
compare-and-set on the entry), so a restart never fires one twice. What came
due while the server was down fires once on the next start, marked late; a
recurring entry then moves to its next future occurrence, so there is no
burst of catch-up firings. A job or question missed by more than
``STALE_AFTER`` is not run late: its firing says it was missed.

Each firing is an alert the dashboard shows (toast, desktop notification)
until the user marks it done or snoozes it; a snoozed alert comes back.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Iterable

from . import config

KINDS = ("reminder", "timer", "job", "prompt")
STATUSES = ("scheduled", "fired", "done", "cancelled", "missed")
CREATORS = ("chat", "voice", "dashboard")
# Queue commands a schedule may run, with the body fields each takes and
# their types. Anything else is refused: no passthrough argv, no shell.
JOBS: dict[str, dict[str, type]] = {
    "ingest": {"dir": str, "force": bool},
    "refresh": {"dir": str, "force": bool},
    "learn": {"desktop": bool},
    "organize": {"no_model": bool},
    "refresh-urls": {},
    "retag": {"pending": bool, "project": str},
}
JOB_NEEDS_DIR = ("ingest", "refresh")
SNOOZE_MINUTES = (5, 10, 30)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MIN_INTERVAL = 5 * 60
MAX_AHEAD = 5 * 366 * 86400
MAX_TIMER = 7 * 86400
# A firing later than this after its time is marked late.
LATE_AFTER = 60.0
# A job or question this late is not run: what it was for has passed.
STALE_AFTER = 12 * 3600.0
MAX_TEXT = 2000
MAX_PAGE = 100


def _now() -> float:
    """The wall clock; tests replace it."""
    return time.time()


class ScheduleError(ValueError):
    """A request the schedule refuses; the message says why, for the user or the model."""


class NotFound(KeyError):
    pass


# ── Time ──────────────────────────────────────────────────────

def _zone(name: str | None) -> tzinfo | None:
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


_zone_cache: tuple[float, str] | None = None


def local_zone_name() -> str:
    """This device's IANA time zone, or "" when it cannot be named
    (then the fixed offset of now stands in, and recurrences do not follow
    daylight-saving changes). Looked up again every five minutes: on
    Windows it asks ``tzutil``."""
    global _zone_cache
    if _zone_cache is None or time.monotonic() - _zone_cache[0] > 300:
        _zone_cache = (time.monotonic(), _detect_zone())
    return _zone_cache[1]


def _detect_zone() -> str:
    candidates = [os.environ.get("TZ", "").lstrip(":")]
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            candidates.append(link.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        candidates.append(Path("/etc/timezone").read_text(encoding="utf-8").strip())
    except OSError:
        pass
    if os.name == "nt":
        try:  # Windows names its zone itself; tzdata maps the common ones.
            import subprocess
            out = subprocess.run(["tzutil", "/g"], capture_output=True, text=True, timeout=2).stdout.strip()
            candidates.append(_WINDOWS_ZONES.get(out, ""))
        except Exception:
            pass
    for name in candidates:
        if name and _zone(name) is not None:
            return name
    return ""


_WINDOWS_ZONES = {
    "UTC": "UTC", "GMT Standard Time": "Europe/London", "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris", "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw", "GTB Standard Time": "Europe/Bucharest",
    "E. Europe Standard Time": "Europe/Chisinau", "FLE Standard Time": "Europe/Kiev",
    "Russian Standard Time": "Europe/Moscow", "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago", "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles", "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai", "Tokyo Standard Time": "Asia/Tokyo",
    "AUS Eastern Standard Time": "Australia/Sydney",
}


def zone_for(name: str | None) -> tzinfo:
    """The named zone, else this device's, else the fixed offset of now."""
    return _zone(name) or _zone(local_zone_name()) or datetime.now().astimezone().tzinfo or timezone.utc


def _local(epoch: float, zone: tzinfo) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone(zone)


def _resolve_wall(day: date, hour: int, minute: int, zone: tzinfo) -> float:
    """A local wall-clock time as an instant. A time the clocks skip (spring
    forward) is moved on by the gap; a time they repeat (fall back) is its
    first occurrence."""
    naive = datetime(day.year, day.month, day.day, hour, minute)
    return naive.replace(tzinfo=zone, fold=0).timestamp()


def describe(epoch: float, zone_name: str | None = None, now: float | None = None) -> str:
    """An instant as the user reads it: "today 10:00", "tomorrow, Mon 9 Nov
    10:00", "Mon 9 Nov 10:00", with the year when it is not this one."""
    zone = zone_for(zone_name)
    moment = _local(epoch, zone)
    today = _local(_now() if now is None else now, zone).date()
    clock = moment.strftime("%H:%M")
    label = f"{moment.strftime('%a')} {moment.day} {moment.strftime('%b')}"
    if moment.year != today.year:
        label += f" {moment.year}"
    if moment.date() == today:
        return f"today {clock}"
    if moment.date() == today + timedelta(days=1):
        return f"tomorrow, {label} {clock}"
    return f"{label} {clock}"


def time_context(now: float | None = None, zone_name: str | None = None) -> str:
    """The current local date, time, weekday and zone, for the chat model's
    instructions: the model cannot know them."""
    name = zone_name if zone_name is not None else local_zone_name()
    moment = _local(_now() if now is None else now, zone_for(name))
    offset = moment.strftime("%z")
    offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    return (f"It is {moment.strftime('%A')} {moment.day} {moment.strftime('%B %Y')} ({moment.strftime('%Y-%m-%d')}), "
            f"{moment.strftime('%H:%M')} local time, time zone {name or 'local'} ({offset}). Give reminder times as "
            f"local ISO-8601 date-times, like {moment.strftime('%Y-%m-%d')}T10:00, or as durations.")


_UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
          "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
          "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
          "d": 86400, "day": 86400, "days": 86400, "w": 604800, "week": 604800, "weeks": 604800}
_PART = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")


def parse_duration(text: Any) -> float:
    """Seconds in "10m", "2h", "1h30m", "90 seconds", "1 day" or ISO "PT10M".
    A bare number is refused: minutes or hours?"""
    if not isinstance(text, str) or not text.strip():
        raise ScheduleError("A duration is required, like 10m, 2h or 1h30m.")
    raw = text.strip().lower()
    iso = re.fullmatch(r"p(?:(\d+)d)?(?:t(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?)?", raw)
    if iso and any(iso.groups()):
        d, h, m, s = (int(g or 0) for g in iso.groups())
        seconds = float(d * 86400 + h * 3600 + m * 60 + s)
    else:
        cleaned = re.sub(r"\band\b|,", " ", raw)
        parts = _PART.findall(cleaned)
        if not parts or _PART.sub("", cleaned).strip():
            raise ScheduleError(f"Could not read the duration {text!r}; use forms like 10m, 2h or 1h30m.")
        seconds = 0.0
        for amount, unit in parts:
            if unit not in _UNITS:
                raise ScheduleError(f"Unknown unit {unit!r} in {text!r}; use s, m, h or d.")
            seconds += float(amount) * _UNITS[unit]
    if seconds < 1:
        raise ScheduleError("The duration must be at least one second.")
    return seconds


def resolve_at(text: Any, zone_name: str | None = None, now: float | None = None) -> tuple[float, str]:
    """An ISO-8601 local date-time ("2026-11-09T10:00"), one with an offset,
    or a time of day ("10:00", the next one) as an instant, with a note when
    it was adjusted. A date-time already past is refused."""
    if not isinstance(text, str) or not text.strip():
        raise ScheduleError("A time is required, as a local ISO-8601 date-time like 2026-11-09T10:00.")
    now = _now() if now is None else now
    zone = zone_for(zone_name)
    raw = text.strip().replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} \d", text.strip()) else text.strip()
    note = ""
    clock = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", raw)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if hour > 23 or minute > 59:
            raise ScheduleError(f"{text!r} is not a time of day.")
        today = _local(now, zone).date()
        when = _resolve_wall(today, hour, minute, zone)
        if when <= now:
            when = _resolve_wall(today + timedelta(days=1), hour, minute, zone)
        return when, note
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleError(f"Could not read the time {text!r}; use a local ISO-8601 date-time like 2026-11-09T10:00.")
    if parsed.tzinfo is None:
        when = _resolve_wall(parsed.date(), parsed.hour, parsed.minute, zone)
        shown = _local(when, zone)
        if (shown.hour, shown.minute) != (parsed.hour, parsed.minute):
            note = (f"{parsed.strftime('%H:%M')} does not exist that day (the clocks change), "
                    f"so it is set for {shown.strftime('%H:%M')}.")
    else:
        when = parsed.timestamp()
    if when <= now:
        raise ScheduleError(f"{describe(when, zone_name, now)} has already passed (it is now {describe(now, zone_name, now)}). "
                            "Ask the user which day they mean.")
    if when - now > MAX_AHEAD:
        raise ScheduleError("That is more than five years ahead.")
    return when, note


# ── Recurrence ────────────────────────────────────────────────

def parse_recurrence(value: Any, due: float, zone_name: str | None) -> dict | None:
    """A recurrence as stored, anchored to the first occurrence's local time.

    Accepts None/"none", "daily", "weekdays", "weekly" or "weekly:mon,thu",
    "every 30m"/"every 2h", or the stored dict ({"every": ...})."""
    if value in (None, "", "none", "once"):
        return None
    zone = zone_for(zone_name)
    first = _local(due, zone)
    anchor = first.strftime("%H:%M")
    if isinstance(value, dict):
        every = value.get("every")
        days = value.get("days")
        seconds = value.get("seconds")
    elif isinstance(value, str):
        text = value.strip().lower()
        every, days, seconds = text, None, None
        if text.startswith("weekly"):
            every = "weekly"
            rest = text[len("weekly"):].strip(" :")
            days = [d.strip()[:3] for d in re.split(r"[,\s]+", rest) if d.strip()] if rest else None
        elif text.startswith("every"):
            every, seconds = "interval", parse_duration(text[len("every"):].strip())
        elif text in ("weekday", "weekdays", "every weekday"):
            every = "weekdays"
        elif text in ("day", "every day"):
            every = "daily"
    else:
        raise ScheduleError("recurrence must be none, daily, weekdays, weekly:mon,thu or every <duration>.")
    if every == "daily":
        return {"every": "daily", "time": anchor}
    if every == "weekdays":
        return {"every": "weekdays", "time": anchor}
    if every == "weekly":
        if days is None:
            numbers = [first.weekday()]
        else:
            if not isinstance(days, list) or not days:
                raise ScheduleError("weekly needs its days, like weekly:mon,thu.")
            numbers = []
            for day in days:
                if isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6:
                    numbers.append(day)
                elif isinstance(day, str) and day.strip().lower()[:3] in WEEKDAYS:
                    numbers.append(WEEKDAYS.index(day.strip().lower()[:3]))
                else:
                    raise ScheduleError(f"{day!r} is not a day of the week.")
        return {"every": "weekly", "days": sorted(set(numbers)), "time": anchor}
    if every == "interval":
        if isinstance(seconds, str):
            seconds = parse_duration(seconds)
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < MIN_INTERVAL:
            raise ScheduleError("A repeat interval must be at least 5 minutes.")
        return {"every": "interval", "seconds": int(seconds)}
    raise ScheduleError("recurrence must be none, daily, weekdays, weekly:mon,thu or every <duration>.")


def _allowed_day(rule: dict, day: date) -> bool:
    if rule["every"] == "weekdays":
        return day.weekday() < 5
    if rule["every"] == "weekly":
        return day.weekday() in rule["days"]
    return True


def next_occurrence(rule: dict, previous: float, after: float, zone_name: str | None) -> float:
    """The first occurrence of ``rule`` later than ``after``. An interval
    counts from ``previous``; a daily, weekday or weekly rule keeps its local
    wall-clock time across daylight-saving changes."""
    if rule["every"] == "interval":
        step = float(rule["seconds"])
        if previous > after:
            return previous
        return previous + (int((after - previous) // step) + 1) * step
    zone = zone_for(zone_name)
    hour, minute = (int(part) for part in rule["time"].split(":"))
    day = _local(max(after, previous), zone).date() - timedelta(days=1)
    for _ in range(16):
        if _allowed_day(rule, day):
            when = _resolve_wall(day, hour, minute, zone)
            if when > after:
                return when
        day += timedelta(days=1)
    raise ScheduleError("The recurrence has no next occurrence.")


def first_occurrence(rule: dict | None, due: float, zone_name: str | None) -> float:
    """``due`` itself, or the next day the rule allows ("every weekday at 9"
    asked on a Saturday starts on Monday)."""
    if not rule or rule["every"] in ("daily", "interval"):
        return due
    zone = zone_for(zone_name)
    if _allowed_day(rule, _local(due, zone).date()):
        return due
    return next_occurrence(rule, due, due, zone_name)


def recurrence_text(rule: dict | None) -> str:
    if not rule:
        return ""
    every = rule["every"]
    if every == "daily":
        return f"Every day at {rule['time']}"
    if every == "weekdays":
        return f"Every weekday at {rule['time']}"
    if every == "weekly":
        names = ", ".join(WEEKDAYS[d].capitalize() for d in rule["days"])
        return f"Every {names} at {rule['time']}"
    seconds = int(rule["seconds"])
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return "Every hour" if hours == 1 else f"Every {hours} hours"
    return f"Every {seconds // 60} minutes"


# ── Store ─────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    due_at REAL NOT NULL,
    timezone TEXT NOT NULL DEFAULT '',
    recurrence TEXT,
    workspace TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_fired_at REAL,
    snoozed_until REAL,
    ended_at REAL
);
CREATE INDEX IF NOT EXISTS entries_due ON entries(status, due_at);
CREATE TABLE IF NOT EXISTS firings (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL,
    occurrence_at REAL NOT NULL,
    fired_at REAL NOT NULL,
    late INTEGER NOT NULL DEFAULT 0,
    missed INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    snoozed_until REAL,
    ring INTEGER NOT NULL DEFAULT 1,
    alerted_at REAL NOT NULL,
    outcome TEXT NOT NULL DEFAULT '{}',
    UNIQUE(entry_id, occurrence_at)
);
CREATE INDEX IF NOT EXISTS firings_state ON firings(state, snoozed_until);
"""

_lock = threading.RLock()


def db_path() -> Path:
    return config.get_data_dir() / "schedule.sqlite3"


@contextmanager
def _db():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def _entry(row: sqlite3.Row, alert: dict | None = None, now: float | None = None) -> dict:
    entry = dict(row)
    entry["recurrence"] = json.loads(entry["recurrence"]) if entry["recurrence"] else None
    entry["payload"] = json.loads(entry["payload"] or "{}")
    entry["repeat"] = recurrence_text(entry["recurrence"])
    entry["when"] = describe(entry["due_at"], entry["timezone"], now)
    entry["alert"] = alert
    return entry


def _firing(row: sqlite3.Row) -> dict:
    firing = dict(row)
    firing["late"], firing["missed"] = bool(firing["late"]), bool(firing["missed"])
    firing["outcome"] = json.loads(firing["outcome"] or "{}")
    return firing


def _text(value: Any, name: str, *, required: bool = False, limit: int = MAX_TEXT) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ScheduleError(f"{name} must be text.")
    value = value.strip()
    if required and not value:
        raise ScheduleError(f"{name} is required.")
    if len(value) > limit:
        raise ScheduleError(f"{name} is longer than {limit} characters.")
    return value


def validate_job(payload: Any, workspace: str | None) -> dict:
    """A job's queue body, checked against the allow-list."""
    if not isinstance(payload, dict):
        raise ScheduleError("A job needs its command.")
    command = payload.get("command")
    if command not in JOBS:
        raise ScheduleError(f"Only these jobs can be scheduled: {', '.join(JOBS)}.")
    fields = JOBS[command]
    body: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "command":
            continue
        if key not in fields:
            raise ScheduleError(f"{command} does not take {key!r}.")
        if value is None or value == "":
            continue
        if not isinstance(value, fields[key]) or (fields[key] is not bool and isinstance(value, bool)):
            raise ScheduleError(f"{key} must be {'true or false' if fields[key] is bool else 'text'}.")
        body[key] = value.strip() if isinstance(value, str) else value
    if command in JOB_NEEDS_DIR:
        folder = body.get("dir")
        if not folder:
            raise ScheduleError(f"{command} needs the folder to index.")
        if not os.path.isabs(os.path.expanduser(folder)):
            raise ScheduleError("The folder must be an absolute path.")
        body["dir"] = os.path.normpath(os.path.expanduser(folder))
        from .workspace_policy import unapproved_root
        try:
            refused = unapproved_root(body["dir"], workspace)
        except ValueError as error:
            raise ScheduleError(f"{body['dir']}: {error}.")
        if refused:
            raise ScheduleError(f"{refused['root']} is not an approved folder of workspace {refused['workspace']!r}; "
                                "approve it on the dashboard first.")
    return {"command": command, **body}


def _payload(kind: str, payload: Any, workspace: str, seconds: float | None) -> dict:
    if kind == "job":
        return validate_job(payload, workspace)
    if payload is not None and not isinstance(payload, dict):
        raise ScheduleError("payload must be an object.")
    payload = payload or {}
    if kind == "prompt":
        return {"question": _text(payload.get("question"), "The question", required=True)}
    if kind == "timer":
        return {"seconds": int(seconds or payload.get("seconds") or 0)}
    return {}


def _when(at: Any, in_: Any, zone_name: str, now: float) -> tuple[float, str, float | None]:
    if at and in_:
        raise ScheduleError("Give either a time (at) or a duration (in), not both.")
    if in_:
        seconds = parse_duration(in_)
        if seconds > MAX_AHEAD:
            raise ScheduleError("That is more than five years ahead.")
        return now + seconds, "", seconds
    if at:
        when, note = resolve_at(at, zone_name, now)
        return when, note, None
    raise ScheduleError("Say when: a time (at) or a duration (in).")


def create(kind: str, *, title: str = "", message: str = "", at: Any = None, in_: Any = None,
           recurrence: Any = None, payload: Any = None, timezone_name: str | None = None,
           workspace: str | None = None, created_by: str = "dashboard", now: float | None = None) -> dict:
    """Validate and store a new entry; returns it with ``note`` when its time was adjusted."""
    now = _now() if now is None else now
    if kind not in KINDS:
        raise ScheduleError(f"kind must be one of {', '.join(KINDS)}.")
    if created_by not in CREATORS:
        raise ScheduleError(f"created_by must be one of {', '.join(CREATORS)}.")
    zone_name = _text(timezone_name, "timezone", limit=64) or local_zone_name()
    if _zone(zone_name) is None and zone_name:
        raise ScheduleError(f"Unknown time zone {zone_name!r}.")
    workspace = config.get_active_workspace() if not workspace else workspace
    due, note, seconds = _when(at, in_, zone_name, now)
    if kind == "timer":
        if seconds is None:
            raise ScheduleError("A timer needs a duration (in), like 10m.")
        if seconds > MAX_TIMER:
            raise ScheduleError("A timer runs for at most 7 days; set a reminder instead.")
        if recurrence not in (None, "", "none"):
            raise ScheduleError("A timer does not repeat; set a repeating reminder instead.")
    rule = parse_recurrence(recurrence, due, zone_name)
    due = first_occurrence(rule, due, zone_name)
    body = _payload(kind, payload, workspace, seconds)
    title = _text(title, "title", limit=200)
    message = _text(message, "message")
    if not title:
        title = {"reminder": message[:80], "timer": f"Timer {_duration_text(seconds or 0)}",
                 "job": _job_title(body), "prompt": body.get("question", "")[:80]}[kind]
    if not title:
        raise ScheduleError("A reminder needs its message.")
    entry_id = uuid.uuid4().hex
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO entries (id, kind, title, message, due_at, timezone, recurrence, workspace, payload, status, "
            "created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?, 'scheduled', ?,?,?)",
            (entry_id, kind, title, message, due, zone_name, json.dumps(rule) if rule else None, workspace,
             json.dumps(body), created_by, now, now))
    changed()
    return {**get(entry_id, now=now), "note": note}


def _job_title(body: dict) -> str:
    command = body.get("command", "")
    label = {"ingest": "Index", "refresh": "Re-index", "learn": "Learn conversations", "organize": "Organize memories",
             "refresh-urls": "Refresh URLs", "retag": "Retag memories"}.get(command, command)
    return f"{label} {body['dir']}" if body.get("dir") else label


def _duration_text(seconds: float) -> str:
    seconds = int(round(seconds))
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size}{unit}")
            seconds %= size
    return " ".join(parts) or "0s"


def _pending_alerts(conn, entry_ids: Iterable[str]) -> dict[str, dict]:
    ids = list(entry_ids)
    if not ids:
        return {}
    rows = conn.execute(f"SELECT * FROM firings WHERE state IN ('pending','snoozed') AND entry_id IN "
                        f"({','.join('?' * len(ids))}) ORDER BY fired_at", ids).fetchall()
    return {row["entry_id"]: _firing(row) for row in rows}


def get(entry_id: str, now: float | None = None) -> dict:
    with _lock, _db() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise NotFound(entry_id)
        return _entry(row, _pending_alerts(conn, [entry_id]).get(entry_id), now)


def find(reference: str) -> dict:
    """An entry by its ID, a unique ID prefix of six or more characters, or
    a unique piece of the title or message of an upcoming or alerting one."""
    reference = (reference or "").strip()
    if not reference:
        raise ScheduleError("Say which reminder: its id or words from it.")
    with _lock, _db() as conn:
        rows = conn.execute("SELECT * FROM entries WHERE id=?", (reference,)).fetchall()
        if not rows and len(reference) >= 6 and re.fullmatch(r"[0-9a-f]+", reference):
            rows = conn.execute("SELECT * FROM entries WHERE id LIKE ?", (reference + "%",)).fetchall()
        if not rows:
            needle = f"%{reference.lower()}%"
            rows = conn.execute("SELECT * FROM entries WHERE (status IN ('scheduled','fired')) AND "
                                "(lower(title) LIKE ? OR lower(message) LIKE ?)", (needle, needle)).fetchall()
        if not rows:
            raise ScheduleError(f"No reminder matches {reference!r}.")
        if len(rows) > 1:
            names = "; ".join(f"{row['id'][:8]} {row['title']}" for row in rows[:5])
            raise ScheduleError(f"Several reminders match {reference!r}: {names}. Say which one.")
        return _entry(rows[0], _pending_alerts(conn, [rows[0]["id"]]).get(rows[0]["id"]))


def list_entries(view: str = "upcoming", limit: int = 25, before: str | None = None, now: float | None = None) -> dict:
    """One page of entries: ``upcoming`` (scheduled, soonest first) or
    ``past`` (fired, done, cancelled or missed, latest first). ``next`` is
    the ``before`` cursor of the following page."""
    if view not in ("upcoming", "past"):
        raise ScheduleError("view must be upcoming or past.")
    limit = max(1, min(int(limit), MAX_PAGE))
    upcoming = view == "upcoming"
    key = "due_at" if upcoming else "COALESCE(ended_at, updated_at)"
    where = "status='scheduled'" if upcoming else "status!='scheduled'"
    args: list[Any] = []
    if before:
        value, _, last_id = before.partition(":")
        try:
            value_f = float(value)
        except ValueError:
            raise ScheduleError("Invalid cursor.")
        where += f" AND ({key} {'>' if upcoming else '<'} ? OR ({key} = ? AND id {'>' if upcoming else '<'} ?))"
        args += [value_f, value_f, last_id]
    order = f"{key} {'ASC' if upcoming else 'DESC'}, id {'ASC' if upcoming else 'DESC'}"
    with _lock, _db() as conn:
        rows = conn.execute(f"SELECT *, {key} AS sort_key FROM entries WHERE {where} ORDER BY {order} LIMIT ?",
                            (*args, limit + 1)).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM entries WHERE {'status=' if upcoming else 'status!='}'scheduled'").fetchone()[0]
        page = rows[:limit]
        alerts = _pending_alerts(conn, [row["id"] for row in page])
    entries = []
    for row in page:
        entry = _entry(row, alerts.get(row["id"]), now)
        entry.pop("sort_key", None)
        entries.append(entry)
    last = page[-1] if len(rows) > limit else None
    return {"entries": entries, "next": f"{last['sort_key']!r}:{last['id']}" if last else None, "total": total}


def alerts() -> list[dict]:
    """Firings waiting for the user (pending), latest first, with their entry."""
    with _lock, _db() as conn:
        rows = conn.execute("SELECT f.*, e.kind, e.title, e.message, e.timezone, e.recurrence IS NOT NULL AS repeats "
                            "FROM firings f JOIN entries e ON e.id=f.entry_id WHERE f.state='pending' "
                            "ORDER BY f.alerted_at DESC LIMIT 50").fetchall()
    result = []
    for row in rows:
        alert = _firing(row)
        alert["repeats"] = bool(alert["repeats"])
        alert["when"] = describe(alert["occurrence_at"], alert["timezone"])
        result.append(alert)
    return result


def update(entry_id: str, changes: dict, now: float | None = None) -> dict:
    """Change an upcoming entry's title, message, time, recurrence or payload."""
    now = _now() if now is None else now
    allowed = {"title", "message", "at", "in", "recurrence", "payload", "timezone"}
    unknown = set(changes) - allowed
    if unknown:
        raise ScheduleError(f"Cannot change {', '.join(sorted(unknown))}.")
    current = get(entry_id, now)
    if current["status"] != "scheduled":
        raise ScheduleError("Only an upcoming entry can be changed.")
    zone_name = _text(changes.get("timezone"), "timezone", limit=64) or current["timezone"]
    if zone_name and _zone(zone_name) is None:
        raise ScheduleError(f"Unknown time zone {zone_name!r}.")
    kind = current["kind"]
    due, seconds = current["due_at"], current["payload"].get("seconds")
    if changes.get("at") or changes.get("in"):
        due, _, seconds = _when(changes.get("at"), changes.get("in"), zone_name, now)
        if kind == "timer" and seconds is None:
            raise ScheduleError("A timer needs a duration (in), like 10m.")
    rule = current["recurrence"]
    if "recurrence" in changes or changes.get("at") or changes.get("in"):
        source = changes["recurrence"] if "recurrence" in changes else rule
        if kind == "timer" and source not in (None, "", "none"):
            raise ScheduleError("A timer does not repeat.")
        # A stored rule is anchored again to the (new) time of day.
        rule = parse_recurrence(source, due, zone_name)
        due = first_occurrence(rule, due, zone_name)
    payload = current["payload"]
    if "payload" in changes:
        payload = _payload(kind, changes["payload"], current["workspace"], seconds)
    elif kind == "timer" and seconds:
        payload = {"seconds": int(seconds)}
    title = _text(changes["title"], "title", limit=200) if "title" in changes else current["title"]
    message = _text(changes["message"], "message") if "message" in changes else current["message"]
    if not title:
        raise ScheduleError("title is required.")
    with _lock, _db() as conn:
        updated = conn.execute(
            "UPDATE entries SET title=?, message=?, due_at=?, timezone=?, recurrence=?, payload=?, updated_at=? "
            "WHERE id=? AND status='scheduled'",
            (title, message, due, zone_name, json.dumps(rule) if rule else None, json.dumps(payload), now, entry_id)).rowcount
    if not updated:
        raise ScheduleError("Only an upcoming entry can be changed.")
    changed()
    return get(entry_id, now)


def cancel(entry_id: str, now: float | None = None) -> dict:
    """Stop an entry: it no longer fires, and its waiting alert is put away."""
    now = _now() if now is None else now
    with _lock, _db() as conn:
        row = conn.execute("SELECT status FROM entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise NotFound(entry_id)
        if row["status"] in ("scheduled", "fired"):
            conn.execute("UPDATE entries SET status='cancelled', updated_at=?, ended_at=?, snoozed_until=NULL WHERE id=?",
                         (now, now, entry_id))
        conn.execute("UPDATE firings SET state='done', snoozed_until=NULL WHERE entry_id=? AND state IN ('pending','snoozed')",
                     (entry_id,))
    changed()
    return get(entry_id, now)


def _open_firing(conn, entry_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM firings WHERE entry_id=? AND state IN ('pending','snoozed') "
                       "ORDER BY fired_at DESC LIMIT 1", (entry_id,)).fetchone()
    if row is None:
        raise ScheduleError("Nothing of this entry is waiting for you.")
    return row


def done(entry_id: str, now: float | None = None) -> dict:
    """The user saw the alert. A one-time entry is then done."""
    now = _now() if now is None else now
    with _lock, _db() as conn:
        if conn.execute("SELECT 1 FROM entries WHERE id=?", (entry_id,)).fetchone() is None:
            raise NotFound(entry_id)
        firing = _open_firing(conn, entry_id)
        conn.execute("UPDATE firings SET state='done', snoozed_until=NULL WHERE id=?", (firing["id"],))
        conn.execute("UPDATE entries SET status=CASE WHEN status='fired' THEN 'done' ELSE status END, "
                     "snoozed_until=NULL, updated_at=? WHERE id=?", (now, entry_id))
    changed()
    return get(entry_id, now)


def snooze(entry_id: str, minutes: Any, now: float | None = None) -> dict:
    """Show the alert again in ``minutes`` (1 to 1440)."""
    now = _now() if now is None else now
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or not 1 <= minutes <= 1440:
        raise ScheduleError("Snooze for 1 to 1440 minutes.")
    until = now + float(minutes) * 60
    with _lock, _db() as conn:
        entry = conn.execute("SELECT kind FROM entries WHERE id=?", (entry_id,)).fetchone()
        if entry is None:
            raise NotFound(entry_id)
        if entry["kind"] not in ("reminder", "timer"):
            raise ScheduleError("Only reminders and timers can be snoozed.")
        firing = _open_firing(conn, entry_id)
        conn.execute("UPDATE firings SET state='snoozed', snoozed_until=? WHERE id=?", (until, firing["id"]))
        conn.execute("UPDATE entries SET snoozed_until=?, updated_at=? WHERE id=?", (until, now, entry_id))
    changed()
    return get(entry_id, now)


# ── Firing ────────────────────────────────────────────────────

def next_wake() -> float | None:
    """When the scheduler must next act: the soonest due entry or snooze."""
    with _lock, _db() as conn:
        due = conn.execute("SELECT MIN(due_at) FROM entries WHERE status='scheduled'").fetchone()[0]
        snoozed = conn.execute("SELECT MIN(snoozed_until) FROM firings WHERE state='snoozed'").fetchone()[0]
    times = [t for t in (due, snoozed) if t is not None]
    return min(times) if times else None


def _claim(entry_id: str, expected_due: float, now: float) -> tuple[dict, dict] | None:
    """Record the firing of one occurrence and move the entry on, in one
    transaction, before anything acts on it. None when another process or an
    earlier run already fired it."""
    with _lock, _db() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=? AND status='scheduled' AND due_at=?",
                           (entry_id, expected_due)).fetchone()
        if row is None:
            return None
        entry = _entry(row)
        late = now - expected_due > LATE_AFTER
        missed = entry["kind"] in ("job", "prompt") and now - expected_due > STALE_AFTER
        firing_id = uuid.uuid4().hex
        try:
            conn.execute("INSERT INTO firings (id, entry_id, occurrence_at, fired_at, late, missed, state, alerted_at) "
                         "VALUES (?,?,?,?,?,?, 'pending', ?)",
                         (firing_id, entry_id, expected_due, now, int(late), int(missed), now))
        except sqlite3.IntegrityError:
            return None  # This occurrence fired before.
        if entry["recurrence"]:
            following = next_occurrence(entry["recurrence"], expected_due, now, entry["timezone"])
            conn.execute("UPDATE entries SET due_at=?, last_fired_at=?, updated_at=? WHERE id=?",
                         (following, now, now, entry_id))
        else:
            conn.execute("UPDATE entries SET status=?, last_fired_at=?, ended_at=?, updated_at=? WHERE id=?",
                         ("missed" if missed else "fired", now, now, now, entry_id))
        firing = _firing(conn.execute("SELECT * FROM firings WHERE id=?", (firing_id,)).fetchone())
    return entry, firing


def _record_outcome(firing_id: str, outcome: dict) -> None:
    with _lock, _db() as conn:
        conn.execute("UPDATE firings SET outcome=? WHERE id=?", (json.dumps(outcome), firing_id))


def _wake_snoozed(now: float) -> int:
    with _lock, _db() as conn:
        rows = conn.execute("SELECT id, entry_id FROM firings WHERE state='snoozed' AND snoozed_until<=?", (now,)).fetchall()
        for row in rows:
            conn.execute("UPDATE firings SET state='pending', snoozed_until=NULL, ring=ring+1, alerted_at=? WHERE id=?",
                         (now, row["id"]))
            conn.execute("UPDATE entries SET snoozed_until=NULL WHERE id=?", (row["entry_id"],))
    return len(rows)


Act = Callable[[dict, dict], dict]


def default_act(run_prompt: Callable[[dict], dict] | None = None) -> Act:
    """What a firing does: a job is submitted to the queue in the entry's
    workspace; a question becomes a chat reply via ``run_prompt``; a reminder
    or timer is only its alert."""
    def act(entry: dict, firing: dict) -> dict:
        if firing["missed"]:
            return {"skipped": "missed while Imprint was not running"}
        if entry["kind"] == "job":
            from . import queue as job_queue
            body = {k: v for k, v in entry["payload"].items() if k != "command"}
            submitted = job_queue.submit(entry["payload"]["command"], body, workspace=entry["workspace"] or None)
            return {"job_id": submitted["job_id"], "merged": submitted["merged"]}
        if entry["kind"] == "prompt":
            if run_prompt is None:
                return {"error": "Chat is not available"}
            return run_prompt(entry)
        return {}
    return act


def run_due(act: Act, now: float | None = None) -> list[dict]:
    """Fire every entry due by ``now`` and bring back snoozed alerts; returns the new firings."""
    now = _now() if now is None else now
    _wake_snoozed(now)
    with _lock, _db() as conn:
        due = conn.execute("SELECT id, due_at FROM entries WHERE status='scheduled' AND due_at<=? ORDER BY due_at",
                           (now,)).fetchall()
    fired = []
    for row in due:
        claimed = _claim(row["id"], row["due_at"], now)
        if claimed is None:
            continue
        entry, firing = claimed
        try:
            outcome = act(entry, firing) or {}
        except Exception as error:  # The firing stands; its alert says what failed.
            outcome = {"error": str(error) or type(error).__name__}
        if outcome:
            _record_outcome(firing["id"], outcome)
        fired.append({**firing, "outcome": outcome})
    return fired


class Scheduler:
    """The API process's scheduler thread: sleeps until the next due entry,
    and wakes early when the schedule changes (``changed``)."""

    MAX_SLEEP = 60.0  # Re-check now and then: the wall clock can jump (sleep, clock changes).

    def __init__(self, act: Act, clock: Callable[[], float] | None = None):
        self.act, self.clock = act, clock or _now
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="schedule", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def poke(self) -> None:
        self._wake.set()

    def step(self) -> float:
        """Fire what is due; returns how long to sleep."""
        run_due(self.act, self.clock())
        upcoming = next_wake()
        if upcoming is None:
            return self.MAX_SLEEP
        return max(0.0, min(self.MAX_SLEEP, upcoming - self.clock()))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                wait = self.step()
            except Exception:
                wait = 5.0  # A locked or damaged store: try again shortly.
            self._wake.wait(wait)
            self._wake.clear()


_scheduler: Scheduler | None = None


def changed() -> None:
    """Wake the running scheduler: an entry was added or changed."""
    if _scheduler is not None:
        _scheduler.poke()


def start(run_prompt: Callable[[dict], dict] | None = None) -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler(default_act(run_prompt))
        _scheduler.start()
    return _scheduler


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None


# ── Chat and voice tools ──────────────────────────────────────
# Each returns the text the model reads, restating the resolved time in the
# user's local time so it can confirm it.

def _confirm(entry: dict, verb: str) -> str:
    when = describe(entry["due_at"], entry["timezone"])
    repeat = f" ({entry['repeat'].lower()})" if entry["repeat"] else ""
    note = f" {entry['note']}" if entry.get("note") else ""
    return f"{verb} for {when}{repeat}. id {entry['id'][:8]}.{note}"


def _tool_error(error: Exception) -> str:
    return f"[tool error] {error}"


def tool_set_reminder(workspace: str = "", created_by: str = "chat", **args: Any) -> str:
    try:
        entry = create("reminder", message=args.get("message", ""), title=args.get("title", ""),
                       at=args.get("at"), in_=args.get("in"), recurrence=args.get("recurrence"),
                       workspace=workspace or None, created_by=created_by)
        return _confirm(entry, f"Reminder \"{entry['message'] or entry['title']}\" set")
    except ScheduleError as error:
        return _tool_error(error)


def tool_set_timer(workspace: str = "", created_by: str = "chat", **args: Any) -> str:
    try:
        entry = create("timer", title=args.get("label", ""), in_=args.get("duration"),
                       workspace=workspace or None, created_by=created_by)
        return _confirm(entry, f"Timer of {_duration_text(entry['payload']['seconds'])} started; it ends")
    except ScheduleError as error:
        return _tool_error(error)


def tool_schedule_job(workspace: str = "", created_by: str = "chat", **args: Any) -> str:
    try:
        payload: dict[str, Any] = {"command": args.get("command")}
        if args.get("folder"):
            payload["dir"] = args["folder"]
        entry = create("job", at=args.get("at"), in_=args.get("in"), recurrence=args.get("recurrence"),
                       payload=payload, workspace=workspace or None, created_by=created_by)
        return _confirm(entry, f"Job \"{entry['title']}\" scheduled")
    except ScheduleError as error:
        return _tool_error(error)


def tool_schedule_prompt(workspace: str = "", created_by: str = "chat", **args: Any) -> str:
    try:
        entry = create("prompt", at=args.get("at"), in_=args.get("in"), recurrence=args.get("recurrence"),
                       payload={"question": args.get("question")}, workspace=workspace or None, created_by=created_by)
        return _confirm(entry, "Question scheduled; the answer will appear in a new chat")
    except ScheduleError as error:
        return _tool_error(error)


def tool_list_reminders(workspace: str = "", created_by: str = "chat", **args: Any) -> str:
    limit = args.get("limit") if isinstance(args.get("limit"), int) else 10
    page = list_entries("upcoming", limit=max(1, min(limit, 25)))
    waiting = alerts()
    lines = []
    for alert in waiting:
        lines.append(f"- due now: {alert['title']} (id {alert['entry_id'][:8]}, {alert['kind']}, was due {alert['when']})")
    for entry in page["entries"]:
        repeat = f", {entry['repeat'].lower()}" if entry["repeat"] else ""
        lines.append(f"- {entry['when']}: {entry['title']} (id {entry['id'][:8]}, {entry['kind']}{repeat})")
    if not lines:
        return "Nothing is scheduled."
    more = page["total"] - len(page["entries"])
    return "\n".join(lines) + (f"\n…and {more} more on the dashboard's Reminders page." if more > 0 else "")


def tool_cancel_reminder(workspace: str = "", created_by: str = "chat", **args: Any) -> str:
    try:
        entry = find(str(args.get("id") or args.get("match") or ""))
        if entry["status"] not in ("scheduled", "fired"):
            return f"\"{entry['title']}\" is already {entry['status']}."
        cancel(entry["id"])
        return f"Cancelled \"{entry['title']}\" (was {describe(entry['due_at'], entry['timezone'])})."
    except ScheduleError as error:
        return _tool_error(error)
