"""A paired sender prepares the next chunk while the other device stores the
last one (ADR 0122): one chunk ahead at most, the same frames in the same
order, and nothing left running when the send ends early."""
import asyncio
import json
import threading
import time
import unittest
from unittest.mock import patch

from emulation import slack, until
from imprint import sync_delta


class Receiver:
    """The other device: stores each chunk for ``store`` seconds once its
    done line arrives, says it is storing, then acknowledges it. An older
    release (``storing=False``) only acknowledges."""

    def __init__(self, store=0.0, storing=True):
        self.store, self.storing = store, storing
        self.frames, self.log = [], []
        self.replies: asyncio.Queue = asyncio.Queue()
        self.chunks = 0

    async def send_line(self, line):
        self.frames.append(json.loads(line))
        if self.frames[-1]["kind"] == "done":
            self.chunks += 1
            asyncio.get_running_loop().create_task(self._store(self.chunks))

    async def _store(self, seq):
        self.log.append(("store", seq))
        if self.storing:
            await self.replies.put({"kind": "storing", "seq": seq})
        await asyncio.sleep(self.store)
        self.log.append(("ack", seq))
        await self.replies.put({"kind": "ack", "seq": seq})

    async def recv(self, timeout):
        return await asyncio.wait_for(self.replies.get(), timeout)

    async def send(self, frame):
        self.frames.append(frame)


class Export:
    """export_chunks with a slow Privacy Filter: each chunk takes ``filter``
    seconds of thread time, checked for a stop every 10 ms like the real
    slices. ``fail`` raises in that chunk; ``block`` makes that chunk take
    until stopped."""

    def __init__(self, receiver, count, filter=0.0, fail=None, block=None):
        self.receiver, self.count, self.filter, self.fail, self.block = receiver, count, filter, fail, block
        self.prepared = self.running = 0
        self.most_ahead = 0
        self.lock = threading.Lock()

    def __call__(self, workspace, keys, stop=None):
        for n in range(1, self.count + 1):
            with self.lock:
                self.running += 1
            try:
                self.receiver.log.append(("prepare", n))
                ends = time.monotonic() + (slack(60) if n == self.block else self.filter)
                while time.monotonic() < ends:
                    if stop is not None and stop.is_set():
                        self.receiver.log.append(("stopped", n))
                        raise sync_delta.Stopped()
                    time.sleep(0.01)
                if n == self.fail:
                    raise OSError("the disk went away")
            finally:
                with self.lock:
                    self.running -= 1
            self.prepared += 1
            self.most_ahead = max(self.most_ahead, self.prepared - self.receiver.chunks)
            yield sync_delta._stream(memories=[{"id": f"{n:016x}", "content": f"chunk {n}", "vector": [0.0]}])


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def send(self, receiver, export, cancel=None):
        events = []
        with patch.object(sync_delta, "export_chunks", side_effect=export):
            async for event in sync_delta._send(receiver, "default", {"memories": ["x"], "facts": []},
                                                cancel or asyncio.Event()):
                events.append(event)
        return events

    def kinds(self, receiver):
        return [frame["kind"] for frame in receiver.frames]

    async def test_the_next_chunk_is_filtered_while_the_last_is_stored(self):
        chunks, work = 4, 0.4
        receiver = Receiver(store=work)
        export = Export(receiver, chunks, filter=work)
        started = time.monotonic()
        events = await self.send(receiver, export)
        elapsed = time.monotonic() - started
        # Chunk n+1 was being prepared before chunk n was acknowledged.
        for n in range(1, chunks):
            self.assertLess(receiver.log.index(("prepare", n + 1)), receiver.log.index(("ack", n)))
        self.assertEqual(export.most_ahead, 1)  # One chunk ahead at most.
        # About (chunks + 1) * work, where one after the other takes 2 * chunks * work.
        self.assertLess(elapsed, slack(2 * chunks * work * 0.8))
        self.assertEqual(receiver.frames[-1], {"kind": "end", "chunks": chunks})
        self.assertIn({"type": "status", "status": "storing"}, events)
        self.assertEqual([e["done"] for e in events if e["type"] == "progress"], [1, 2, 3, 4])

    async def test_the_frames_are_those_of_an_unpipelined_sender_for_an_older_peer(self):
        receiver = Receiver(storing=False)
        await self.send(receiver, Export(receiver, 3))
        stream = ["meta", "batch", "done"]
        self.assertEqual(self.kinds(receiver), stream * 3 + ["end"])
        self.assertEqual(receiver.frames[-1]["chunks"], 3)
        self.assertNotIn("privacy_filter", json.dumps(receiver.frames))  # A CPU sender marks nothing.

    async def test_cancelling_mid_prefetch_drops_the_chunk_and_leaves_no_thread(self):
        receiver = Receiver(store=slack(60))
        export = Export(receiver, 3, block=2)
        task = asyncio.ensure_future(self.send(receiver, export))
        self.assertTrue(await until(lambda: ("prepare", 2) in receiver.log))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(export.running, 0)  # The preparing thread finished before the send ended.
        self.assertIn(("stopped", 2), receiver.log)
        self.assertEqual(self.kinds(receiver), ["meta", "batch", "done"])  # Chunk 2 never sent, no end.

    async def test_the_cancel_event_stops_a_send_waiting_for_its_next_chunk(self):
        receiver = Receiver(store=0.2)
        export = Export(receiver, 3, block=2)
        cancel = asyncio.Event()
        task = asyncio.ensure_future(self.send(receiver, export, cancel))
        self.assertTrue(await until(lambda: ("ack", 1) in receiver.log))
        cancel.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, slack(10))
        self.assertEqual(export.running, 0)
        self.assertIn(("stopped", 2), receiver.log)
        self.assertNotIn("end", self.kinds(receiver))

    async def test_an_error_preparing_the_next_chunk_fails_the_send_after_the_last_ack(self):
        receiver = Receiver(store=0.1)
        export = Export(receiver, 3, fail=2)
        with self.assertRaisesRegex(OSError, "the disk went away"):
            await self.send(receiver, export)
        self.assertEqual(self.kinds(receiver), ["meta", "batch", "done"])
        self.assertIn(("ack", 1), receiver.log)

    async def test_a_refused_chunk_stops_the_prefetch(self):
        receiver = Receiver(store=0.0)

        async def refuse(seq):
            await receiver.replies.put({"kind": "ack", "seq": seq, "error": "disk full"})
        receiver._store = refuse
        export = Export(receiver, 3, block=2)
        with self.assertRaisesRegex(RuntimeError, "disk full"):
            await asyncio.wait_for(self.send(receiver, export), slack(10))
        self.assertEqual(export.running, 0)
        self.assertIn(("stopped", 2), receiver.log)


class ExportStopTests(unittest.TestCase):
    def test_a_stop_ends_the_filter_pass_at_the_next_slice(self):
        stop = threading.Event()
        passes = []

        def filtered(records):
            passes.append(len(records))
            stop.set()
            return records
        payloads = [{"_mid": f"{n:016x}", "content": "x"} for n in range(60)]
        with patch("imprint.vectorstore.retrieve_payloads", return_value=payloads), \
             patch("imprint.vectorstore.filtered_for_peer", side_effect=filtered):
            with self.assertRaises(sync_delta.Stopped):
                next(sync_delta.export_chunks("default", {"memories": ["x"] * 60}, stop=stop))
        self.assertEqual(passes, [sync_delta.FILTER_SLICE])


if __name__ == "__main__":
    unittest.main()
