"""Chat replies run until they are answered or stopped (ADR 0145): leaving the
page, opening another chat or a dropped connection only stops watching."""
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from emulation import slack
from imprint import api, chat_replies, chat_store


def events(lines):
    return [json.loads(line[6:]) for line in lines if line.startswith("data: ")]


class ChatRepliesOutliveTheirRequestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (patch.dict(os.environ, {"IMPRINT_DATA_DIR": directory.name}),
                        patch.dict(chat_store._conns, clear=True),
                        patch.dict(chat_replies._replies, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(chat_store._close_all)
        self.client = TestClient(api.app, base_url="http://127.0.0.1:8420", client=("127.0.0.1", 1234))
        self.sid = chat_store.create_session()
        self.release = threading.Event()
        self.cancelled = []

        def reply(prior, message, workspace="", summary=None, cancel_event=None):
            self.cancelled.append(cancel_event)
            yield {"type": "token", "text": "Billing is on "}
            while not self.release.wait(.01):
                if cancel_event is not None and cancel_event.is_set():
                    return
            yield {"type": "token", "text": "port 8101."}
            yield {"type": "assistant_message", "text": "Billing is on port 8101."}
            yield {"type": "done"}
        patcher = patch.object(api.chat_mod, "stream_reply", side_effect=reply)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.release.set)

    def wait_until(self, check):
        deadline = time.monotonic() + slack(10)
        while not check() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(check())

    def ask(self, message="Where is billing?"):
        """Send a question and read until its first token, then leave."""
        lines = []
        with self.client.stream("POST", "/api/chat", json={"session_id": self.sid, "message": message}) as response:
            self.assertEqual(response.status_code, 200)
            for line in response.iter_lines():
                lines.append(line)
                if '"token"' in line:
                    break
        return lines

    def test_a_reply_goes_on_after_the_page_leaves_and_is_kept_in_the_chat(self):
        self.ask()
        self.assertTrue(self.client.get(f"/api/chat/sessions/{self.sid}").json()["replying"])
        self.assertEqual([s["replying"] for s in self.client.get("/api/chat/sessions").json()["sessions"]], [True])
        self.release.set()
        self.wait_until(lambda: chat_replies.current(self.sid) is None)
        session = self.client.get(f"/api/chat/sessions/{self.sid}").json()
        self.assertFalse(session["replying"])
        self.assertEqual([(m["role"], m["content"]) for m in session["messages"]],
                         [("user", "Where is billing?"), ("assistant", "Billing is on port 8101.")])
        self.assertFalse(self.cancelled[0].is_set())

    def test_a_page_that_comes_back_sees_the_reply_from_its_start_and_the_rest(self):
        self.ask()
        with self.client.stream("GET", f"/api/chat/sessions/{self.sid}/reply") as response:
            self.assertEqual(response.status_code, 200)
            lines = response.iter_lines()
            first = next(line for line in lines if line.startswith("data: "))
            self.assertEqual(json.loads(first[6:]), {"type": "token", "text": "Billing is on "})
            self.release.set()
            rest = events(list(lines))
        self.assertEqual([event["type"] for event in rest], ["token", "assistant_message", "done"])
        self.assertEqual(self.client.get(f"/api/chat/sessions/{self.sid}/reply").status_code, 404)

    def test_only_the_users_stop_cancels_and_what_was_written_stays(self):
        self.ask()
        self.assertEqual(self.client.post("/api/chat", json={"session_id": self.sid, "message": "Again?"}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/chat/sessions/{self.sid}/stop").json(), {"ok": True})
        self.wait_until(lambda: chat_replies.current(self.sid) is None)
        self.assertTrue(self.cancelled[0].is_set())
        messages = chat_store.get_messages(self.sid)
        self.assertEqual([(m["role"], m["content"]) for m in messages], [("user", "Where is billing?"), ("assistant", "Billing is on")])
        self.assertEqual(self.client.post(f"/api/chat/sessions/{self.sid}/stop").json(), {"ok": False})
        # The chat takes a new question once the stopped reply has ended.
        self.release.set()
        lines = []
        with self.client.stream("POST", "/api/chat", json={"session_id": self.sid, "message": "And now?"}) as response:
            lines = list(response.iter_lines())
        self.assertEqual(events(lines)[-1], {"type": "done"})


if __name__ == "__main__":
    unittest.main()
