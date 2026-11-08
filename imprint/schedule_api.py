"""The dashboard's /api/schedule: reminders, timers and scheduled jobs (ADR 0158).

Every write is validated by ``imprint.schedule``; a job is only ever one of
its allow-listed queue commands.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import schedule

router = APIRouter(prefix="/api/schedule")


def _refused(error: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": str(error)}, status_code=status)


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise schedule.ScheduleError("A JSON object is required.")
    if not isinstance(body, dict):
        raise schedule.ScheduleError("A JSON object is required.")
    return body


@router.get("")
def list_entries(view: str = "upcoming", limit: int = 25, before: str | None = None):
    """One page of ``upcoming`` (soonest first) or ``past`` entries (latest
    first); ``next`` is the ``before`` of the following page."""
    try:
        return schedule.list_entries(view, limit, before)
    except schedule.ScheduleError as error:
        return _refused(error)


@router.get("/alerts")
def list_alerts():
    """Firings waiting for the user, which the dashboard shows as toasts and
    desktop notifications until they are done or snoozed."""
    return {"alerts": schedule.alerts(), "snooze_minutes": list(schedule.SNOOZE_MINUTES)}


@router.get("/options")
def options():
    """What the Reminders page offers: kinds, jobs and this device's zone."""
    return {"kinds": list(schedule.KINDS), "jobs": {name: sorted(fields) for name, fields in schedule.JOBS.items()},
            "timezone": schedule.local_zone_name(), "snooze_minutes": list(schedule.SNOOZE_MINUTES)}


@router.post("")
async def create(request: Request):
    try:
        body = await _body(request)
        unknown = set(body) - {"kind", "title", "message", "at", "in", "recurrence", "payload", "timezone"}
        if unknown:
            raise schedule.ScheduleError(f"Unknown fields: {', '.join(sorted(unknown))}.")
        return await asyncio.to_thread(
            schedule.create, body.get("kind", ""), title=body.get("title", ""), message=body.get("message", ""),
            at=body.get("at"), in_=body.get("in"), recurrence=body.get("recurrence"), payload=body.get("payload"),
            timezone_name=body.get("timezone"), created_by="dashboard")
    except schedule.ScheduleError as error:
        return _refused(error)


@router.get("/{entry_id}")
def get(entry_id: str):
    try:
        return schedule.get(entry_id)
    except schedule.NotFound:
        return JSONResponse({"error": "not found"}, status_code=404)


@router.patch("/{entry_id}")
async def update(entry_id: str, request: Request):
    try:
        return await asyncio.to_thread(schedule.update, entry_id, await _body(request))
    except schedule.NotFound:
        return JSONResponse({"error": "not found"}, status_code=404)
    except schedule.ScheduleError as error:
        return _refused(error)


@router.post("/{entry_id}/snooze")
async def snooze(entry_id: str, request: Request):
    try:
        body = await _body(request)
        return schedule.snooze(entry_id, body.get("minutes"))
    except schedule.NotFound:
        return JSONResponse({"error": "not found"}, status_code=404)
    except schedule.ScheduleError as error:
        return _refused(error, 409 if "waiting" in str(error) else 400)


@router.post("/{entry_id}/done")
def done(entry_id: str):
    try:
        return schedule.done(entry_id)
    except schedule.NotFound:
        return JSONResponse({"error": "not found"}, status_code=404)
    except schedule.ScheduleError as error:
        return _refused(error, 409)


@router.post("/{entry_id}/cancel")
def cancel(entry_id: str):
    try:
        return schedule.cancel(entry_id)
    except schedule.NotFound:
        return JSONResponse({"error": "not found"}, status_code=404)
