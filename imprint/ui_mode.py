"""Which dashboard an installation opens (ADRs 0182, 0183).

``ui.mode`` and ``ui.tutorials`` are ordinary settings. The dashboard API
settles them once, at its first start after they arrived: an installation
that was already in use keeps the dashboard it knows (advanced, no
tutorials), and a new one starts simple with tutorials. The choice is written
to config.json either way, so later starts find it made and a new
installation is not taken for a used one once it has history.
"""
from __future__ import annotations

import sqlite3

from . import config
from .config_schema import read_config, resolve, write_config

# Evidence of earlier use: a job the queue ran, or an agent session.
_HISTORY = (("queue.sqlite3", "jobs"), ("agent_activity.sqlite3", "agent_sessions"))


def _has_rows(name: str, table: str) -> bool:
    path = config.get_data_dir() / name
    if not path.is_file():
        return False
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1)
        try:
            return conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def used_before() -> bool:
    return any(_has_rows(name, table) for name, table in _HISTORY)


def settle() -> str | None:
    """Write this installation's first ``ui.mode`` and return it; None when
    config.json already holds one. A used installation also gets
    ``ui.tutorials`` off unless it was set."""
    cfg = read_config()
    ui = cfg.get("ui")
    if isinstance(ui, dict) and "mode" in ui:
        return None
    ui = dict(ui) if isinstance(ui, dict) else {}
    used = used_before()
    ui["mode"] = "advanced" if used else "simple"
    if used:
        ui.setdefault("tutorials", False)
    cfg["ui"] = ui
    write_config(cfg)
    return ui["mode"]


def state() -> dict:
    """What the dashboard shell needs before it draws its navigation."""
    mode, _ = resolve("ui.mode")
    tutorials, _ = resolve("ui.tutorials")
    return {"mode": mode if mode in ("simple", "advanced") else "simple", "tutorials": bool(tutorials)}
