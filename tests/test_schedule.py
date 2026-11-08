"""Reminders, timers and scheduled jobs (ADR 0158): due-time resolution,
recurrence, firing exactly once across restarts, missed entries, the API's
validation, and the chat and voice tools. Every clock is injected."""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from imprint import chat, config, schedule

ZONE = "Europe/Bucharest"


def at(text, zone=ZONE):
    """A local wall-clock time in ``zone`` as epoch seconds."""
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(zone)).timestamp()


def local(epoch, zone=ZONE):
    return datetime.fromtimestamp(epoch, ZoneInfo(zone)).strftime("%a %Y-%m-%d %H:%M")


class Case(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name) / "data"
        for item in (patch.dict(os.environ, {"IMPRINT_DATA_DIR": str(self.data), "IMPRINT_WORKSPACE": ""}),
                     patch.object(schedule, "local_zone_name", return_value=ZONE)):
            item.start()
            self.addCleanup(item.stop)
        config.register_workspace("alpha")


class DueTimeTests(Case):
    NOW = at("2026-11-08T14:05")  # Sunday

    def test_a_local_date_time_resolves_in_the_zone(self):
        when, note = schedule.resolve_at("2026-11-09T10:00", ZONE, self.NOW)
        self.assertEqual(local(when), "Mon 2026-11-09 10:00")
        self.assertEqual(note, "")

    def test_an_offset_is_honoured(self):
        when, _ = schedule.resolve_at("2026-11-09T10:00+00:00", ZONE, self.NOW)
        self.assertEqual(local(when), "Mon 2026-11-09 12:00")

    def test_a_time_of_day_is_the_next_one(self):
        later, _ = schedule.resolve_at("15:30", ZONE, self.NOW)
        self.assertEqual(local(later), "Sun 2026-11-08 15:30")
        passed, _ = schedule.resolve_at("10:00", ZONE, self.NOW)
        self.assertEqual(local(passed), "Mon 2026-11-09 10:00")

    def test_a_passed_date_time_is_refused_with_a_question(self):
        with self.assertRaises(schedule.ScheduleError) as refused:
            schedule.resolve_at("2026-11-08T10:00", ZONE, self.NOW)
        self.assertIn("already passed", str(refused.exception))
        self.assertIn("Ask the user", str(refused.exception))

    def test_relative_durations(self):
        self.assertEqual(schedule.parse_duration("10m"), 600)
        self.assertEqual(schedule.parse_duration("1h30m"), 5400)
        self.assertEqual(schedule.parse_duration("2 hours and 5 minutes"), 7500)
        self.assertEqual(schedule.parse_duration("PT45M"), 2700)
        for bad in ("10", "", "soon", "5 parsecs"):
            with self.assertRaises(schedule.ScheduleError, msg=bad):
                schedule.parse_duration(bad)

    def test_a_time_the_clocks_skip_moves_on_and_says_so(self):
        # Romania springs forward at 03:00 on 28 March 2027: 03:30 does not exist.
        when, note = schedule.resolve_at("2027-03-28T03:30", ZONE, self.NOW)
        self.assertEqual(local(when), "Sun 2027-03-28 04:30")
        self.assertIn("does not exist", note)

    def test_a_repeated_time_is_its_first_occurrence(self):
        # Romania falls back at 04:00 on 31 October 2027: 03:30 happens twice.
        when, _ = schedule.resolve_at("2027-10-31T03:30", ZONE, self.NOW)
        self.assertEqual(datetime.fromtimestamp(when, ZoneInfo(ZONE)).utcoffset().total_seconds(), 3 * 3600)

    def test_describe_says_today_and_tomorrow(self):
        self.assertEqual(schedule.describe(at("2026-11-08T18:00"), ZONE, self.NOW), "today 18:00")
        self.assertEqual(schedule.describe(at("2026-11-09T10:00"), ZONE, self.NOW), "tomorrow, Mon 9 Nov 10:00")
        self.assertEqual(schedule.describe(at("2027-01-04T10:00"), ZONE, self.NOW), "Mon 4 Jan 2027 10:00")


class RecurrenceTests(Case):
    def test_weekdays_skip_the_weekend(self):
        rule = schedule.parse_recurrence("weekdays", at("2026-11-13T09:00"), ZONE)  # a Friday
        following = schedule.next_occurrence(rule, at("2026-11-13T09:00"), at("2026-11-13T09:00"), ZONE)
        self.assertEqual(local(following), "Mon 2026-11-16 09:00")

    def test_weekdays_asked_on_a_saturday_start_on_monday(self):
        rule = schedule.parse_recurrence("weekdays", at("2026-11-14T09:00"), ZONE)
        self.assertEqual(local(schedule.first_occurrence(rule, at("2026-11-14T09:00"), ZONE)), "Mon 2026-11-16 09:00")

    def test_weekly_on_days(self):
        rule = schedule.parse_recurrence("weekly:mon,thu", at("2026-11-09T08:00"), ZONE)
        self.assertEqual(rule, {"every": "weekly", "days": [0, 3], "time": "08:00"})
        second = schedule.next_occurrence(rule, at("2026-11-09T08:00"), at("2026-11-09T08:00"), ZONE)
        third = schedule.next_occurrence(rule, second, second, ZONE)
        self.assertEqual([local(second), local(third)], ["Thu 2026-11-12 08:00", "Mon 2026-11-16 08:00"])

    def test_every_n_counts_from_the_previous_occurrence(self):
        start = at("2026-11-08T10:00")
        rule = schedule.parse_recurrence("every 2h", start, ZONE)
        self.assertEqual(rule, {"every": "interval", "seconds": 7200})
        self.assertEqual(schedule.next_occurrence(rule, start, start, ZONE), start + 7200)
        # Five hours late, it skips to the next future slot, not one per missed slot.
        self.assertEqual(schedule.next_occurrence(rule, start, start + 5 * 3600, ZONE), start + 6 * 3600)
        with self.assertRaises(schedule.ScheduleError):
            schedule.parse_recurrence("every 1m", start, ZONE)

    def test_daily_keeps_its_wall_clock_time_across_daylight_saving(self):
        # Romania falls back on 25 October 2026.
        rule = schedule.parse_recurrence("daily", at("2026-10-24T09:00"), ZONE)
        following = schedule.next_occurrence(rule, at("2026-10-24T09:00"), at("2026-10-24T09:00"), ZONE)
        self.assertEqual(local(following), "Sun 2026-10-25 09:00")
        self.assertEqual(following - at("2026-10-24T09:00"), 25 * 3600)

    def test_text(self):
        self.assertEqual(schedule.recurrence_text({"every": "weekdays", "time": "09:00"}), "Every weekday at 09:00")
        self.assertEqual(schedule.recurrence_text({"every": "interval", "seconds": 1800}), "Every 30 minutes")


class FiringTests(Case):
    NOW = at("2026-11-08T14:00")

    def act(self):
        return Mock(return_value={})

    def test_a_reminder_fires_once_and_waits_for_the_user(self):
        entry = schedule.create("reminder", message="call Dan", in_="10m", now=self.NOW)
        act = self.act()
        self.assertEqual(schedule.run_due(act, self.NOW + 599), [])
        fired = schedule.run_due(act, self.NOW + 600)
        self.assertEqual(len(fired), 1)
        self.assertFalse(fired[0]["late"])
        self.assertEqual(schedule.run_due(act, self.NOW + 700), [])
        act.assert_called_once()
        self.assertEqual(schedule.get(entry["id"])["status"], "fired")
        self.assertEqual([a["entry_id"] for a in schedule.alerts()], [entry["id"]])
        schedule.done(entry["id"], now=self.NOW + 800)
        self.assertEqual(schedule.get(entry["id"])["status"], "done")
        self.assertEqual(schedule.alerts(), [])

    def test_exactly_once_across_a_restart(self):
        """A second scheduler (the API restarted, or a second process) never
        fires an occurrence the first one recorded, even if the first died
        before acting on it."""
        entry = schedule.create("job", payload={"command": "refresh-urls"}, in_="1h", now=self.NOW)
        due = self.NOW + 3600
        claimed = schedule._claim(entry["id"], entry["due_at"], due)  # Recorded, then the process died.
        self.assertIsNotNone(claimed)
        act = self.act()
        self.assertEqual(schedule.Scheduler(act, clock=lambda: due + 5).step() >= 0, True)
        act.assert_not_called()
        self.assertIsNone(schedule._claim(entry["id"], entry["due_at"], due + 10))

    def test_missed_while_down_fires_once_late_then_moves_to_the_future(self):
        entry = schedule.create("reminder", message="stand up", at="2026-11-09T09:00", recurrence="weekdays", now=self.NOW)
        # The server was down from Monday morning to Thursday noon.
        act = self.act()
        fired = schedule.run_due(act, at("2026-11-12T12:00"))
        self.assertEqual(len(fired), 1)
        self.assertTrue(fired[0]["late"])
        self.assertEqual(local(schedule.get(entry["id"])["due_at"]), "Fri 2026-11-13 09:00")
        self.assertEqual(schedule.run_due(act, at("2026-11-12T12:01")), [])

    def test_a_stale_job_is_not_run_late(self):
        entry = schedule.create("job", payload={"command": "learn"}, at="2026-11-08T15:00", now=self.NOW)
        submit = Mock(return_value={"job_id": "j", "merged": False})
        with patch("imprint.queue.submit", submit):
            fired = schedule.run_due(schedule.default_act(), at("2026-11-10T15:00"))
        submit.assert_not_called()
        self.assertTrue(fired[0]["missed"])
        self.assertIn("skipped", fired[0]["outcome"])
        self.assertEqual(schedule.get(entry["id"])["status"], "missed")

    def test_a_due_job_goes_to_the_queue_in_its_workspace(self):
        schedule.create("job", payload={"command": "retag", "pending": True}, in_="5m", workspace="alpha", now=self.NOW)
        submit = Mock(return_value={"job_id": "j1", "merged": False})
        with patch("imprint.queue.submit", submit):
            fired = schedule.run_due(schedule.default_act(), self.NOW + 300)
        submit.assert_called_once_with("retag", {"pending": True}, workspace="alpha")
        self.assertEqual(fired[0]["outcome"], {"job_id": "j1", "merged": False})

    def test_a_question_runs_as_a_chat_reply(self):
        schedule.create("prompt", payload={"question": "What did I work on yesterday?"}, in_="1m", now=self.NOW)
        run_prompt = Mock(return_value={"chat": "sid1"})
        fired = schedule.run_due(schedule.default_act(run_prompt), self.NOW + 60)
        self.assertEqual(run_prompt.call_args.args[0]["payload"], {"question": "What did I work on yesterday?"})
        self.assertEqual(schedule.alerts()[0]["outcome"], {"chat": "sid1"})
        self.assertEqual(fired[0]["outcome"], {"chat": "sid1"})

    def test_a_failing_action_is_recorded_on_its_alert(self):
        schedule.create("job", payload={"command": "learn"}, in_="1m", now=self.NOW)
        fired = schedule.run_due(Mock(side_effect=RuntimeError("queue gone")), self.NOW + 60)
        self.assertEqual(fired[0]["outcome"], {"error": "queue gone"})

    def test_snooze_brings_the_alert_back(self):
        entry = schedule.create("timer", in_="10m", now=self.NOW)
        schedule.run_due(self.act(), self.NOW + 600)
        schedule.snooze(entry["id"], 5, now=self.NOW + 610)
        self.assertEqual(schedule.alerts(), [])
        self.assertEqual(schedule.next_wake(), self.NOW + 910)
        schedule.run_due(self.act(), self.NOW + 910)
        [alert] = schedule.alerts()
        self.assertEqual(alert["ring"], 2)

    def test_jobs_cannot_be_snoozed_and_cancel_clears_alerts(self):
        entry = schedule.create("job", payload={"command": "learn"}, in_="1m", now=self.NOW)
        schedule.run_due(self.act(), self.NOW + 60)
        with self.assertRaises(schedule.ScheduleError):
            schedule.snooze(entry["id"], 5, now=self.NOW + 70)
        schedule.cancel(entry["id"], now=self.NOW + 80)
        self.assertEqual(schedule.alerts(), [])

    def test_scheduler_sleeps_until_the_next_due_entry(self):
        scheduler = schedule.Scheduler(self.act(), clock=lambda: self.NOW)
        self.assertEqual(scheduler.step(), schedule.Scheduler.MAX_SLEEP)
        schedule.create("reminder", message="tea", in_="30s", now=self.NOW)
        self.assertEqual(scheduler.step(), 30)


class StoreTests(Case):
    NOW = at("2026-11-08T14:00")

    def test_upcoming_and_past_pages_follow_their_cursors(self):
        ids = [schedule.create("reminder", message=f"r{i}", in_=f"{i + 1}m", now=self.NOW)["id"] for i in range(5)]
        seen, cursor = [], None
        while True:
            page = schedule.list_entries("upcoming", limit=2, before=cursor, now=self.NOW)
            self.assertEqual(page["total"], 5)
            seen += [e["id"] for e in page["entries"]]
            cursor = page["next"]
            if cursor is None:
                break
        self.assertEqual(seen, ids)
        for entry_id in ids[:3]:
            schedule.cancel(entry_id, now=self.NOW + 1)
        past = schedule.list_entries("past", limit=10)
        self.assertEqual(len(past["entries"]), 3)

    def test_update_moves_an_upcoming_entry(self):
        entry = schedule.create("reminder", message="x", in_="1h", now=self.NOW)
        changed = schedule.update(entry["id"], {"at": "2026-11-09T10:00", "recurrence": "daily"}, now=self.NOW)
        self.assertEqual(local(changed["due_at"]), "Mon 2026-11-09 10:00")
        self.assertEqual(changed["repeat"], "Every day at 10:00")
        with self.assertRaises(schedule.ScheduleError):
            schedule.update(entry["id"], {"status": "done"}, now=self.NOW)

    def test_find_by_prefix_or_words(self):
        entry = schedule.create("reminder", message="call Dan", in_="1h", now=self.NOW)
        schedule.create("reminder", message="water plants", in_="2h", now=self.NOW)
        self.assertEqual(schedule.find(entry["id"][:8])["id"], entry["id"])
        self.assertEqual(schedule.find("dan")["id"], entry["id"])
        with self.assertRaises(schedule.ScheduleError):
            schedule.find("a")  # both match


class ApiTests(Case):
    def setUp(self):
        super().setUp()
        from imprint import api
        self.client = TestClient(api.app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))

    def post(self, body):
        return self.client.post("/api/schedule", json=body)

    def test_create_list_and_act(self):
        created = self.post({"kind": "reminder", "message": "call Dan", "in": "10m"})
        self.assertEqual(created.status_code, 200, created.text)
        entry = created.json()
        self.assertEqual(entry["created_by"], "dashboard")
        listed = self.client.get("/api/schedule?view=upcoming").json()
        self.assertEqual([e["id"] for e in listed["entries"]], [entry["id"]])
        self.assertEqual(self.client.post(f"/api/schedule/{entry['id']}/done").status_code, 409)
        self.assertEqual(self.client.post(f"/api/schedule/{entry['id']}/cancel").json()["status"], "cancelled")
        self.assertEqual(self.client.get("/api/schedule/nope").status_code, 404)

    def test_jobs_come_only_from_the_allow_list(self):
        for payload in ({"command": "wipe", "force": True}, {"command": "config", "action": "set"},
                        {"command": "learn", "args": ["--rm", "-rf"]}, {"command": "learn", "desktop": "yes"},
                        {"command": "ingest"}, {"command": "ingest", "dir": "relative/path"}, "learn"):
            response = self.post({"kind": "job", "in": "1h", "payload": payload})
            self.assertEqual(response.status_code, 400, payload)
        ok = self.post({"kind": "job", "in": "1h", "recurrence": "daily", "payload": {"command": "refresh-urls"}})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["payload"], {"command": "refresh-urls"})

    def test_bad_requests_are_refused(self):
        for body in ({"kind": "alarm", "in": "1m", "message": "x"}, {"kind": "reminder", "message": "x"},
                     {"kind": "reminder", "message": "x", "at": "2020-01-01T10:00"},
                     {"kind": "reminder", "message": "x", "in": "1m", "at": "2030-01-01T10:00"},
                     {"kind": "timer", "in": "1m", "recurrence": "daily"},
                     {"kind": "reminder", "message": "x", "in": "1m", "timezone": "Mars/Olympus"},
                     {"kind": "reminder", "message": "x", "in": "1m", "status": "done"}):
            self.assertEqual(self.post(body).status_code, 400, body)
        self.assertEqual(self.client.post("/api/schedule", content=b"[]",
                                          headers={"content-type": "application/json"}).status_code, 400)
        self.assertEqual(self.client.get("/api/schedule?view=sideways").status_code, 400)


class ChatToolTests(Case):
    def test_personal_tools_run_in_voice_while_store_stays_blocked(self):
        offered = {schema["function"]["name"] for schema in chat._tool_schemas(True)}
        self.assertLessEqual({"set_reminder", "set_timer", "schedule_job", "schedule_prompt",
                              "list_reminders", "cancel_reminder"}, offered)
        self.assertFalse({"store", "kg_add", "kg_invalidate", "ingest_url"} & offered)
        result = chat._run_tool("set_timer", {"duration": "10m", "label": "tea"}, "", True)
        self.assertIn("Timer of 10m started", result)
        [entry] = schedule.list_entries()["entries"]
        self.assertEqual((entry["title"], entry["created_by"]), ("tea", "voice"))
        self.assertEqual(chat._run_tool("store", {"content": "x"}, "", True),
                         "Voice mode is read-only. Use the dashboard to make changes.")

    def test_reminder_result_restates_the_local_time(self):
        with patch.object(schedule, "_now", return_value=at("2026-11-08T14:05")):
            result = chat._run_tool("set_reminder", {"message": "call Dan", "at": "2026-11-09T10:00"}, "", False)
            self.assertIn("tomorrow, Mon 9 Nov 10:00", result)
            refused = chat._run_tool("set_reminder", {"message": "call Dan", "at": "2026-11-08T10:00"}, "", False)
            self.assertTrue(refused.startswith("[tool error]"))
            self.assertIn("already passed", refused)
        [entry] = schedule.list_entries()["entries"]
        self.assertEqual(entry["created_by"], "chat")

    def test_cancel_by_words_and_list(self):
        chat._run_tool("set_reminder", {"message": "call Dan", "in": "2h"}, "", False)
        self.assertIn("call Dan", chat._run_tool("list_reminders", {}, "", True))
        self.assertIn("Cancelled", chat._run_tool("cancel_reminder", {"match": "dan"}, "", True))
        self.assertEqual(chat._run_tool("list_reminders", {}, "", True), "Nothing is scheduled.")

    def test_instructions_carry_the_current_time(self):
        line = schedule.time_context(now=at("2026-11-08T14:05"), zone_name=ZONE)
        self.assertIn("It is Sunday 8 November 2026 (2026-11-08), 14:05 local time", line)
        self.assertIn("Europe/Bucharest (UTC+02:00)", line)
        from test_chat_stream import remote_provider, text_chunk
        client, patches = remote_provider([text_chunk("Hello.")])
        with patches[0], patches[1], patches[2], patch.object(schedule, "_now", return_value=at("2026-11-08T14:05")):
            list(chat.stream_reply([], "hi"))
        system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("# Current time\nIt is Sunday 8 November 2026 (2026-11-08), 14:05", system)


if __name__ == "__main__":
    unittest.main()
