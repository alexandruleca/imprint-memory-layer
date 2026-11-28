"""A Dispatched session's claim across paired devices (ADR 0224, Dispatcher 9)."""

import os
from pathlib import Path
import re
import sys
import threading
import time
from unittest.mock import patch

from imprint import sync_pairs, tasks
from imprint.dispatch import claimcheck, dispatched, store

from test_dispatch_run import DispatchCase


def _pair(*, seen=0.0, workspace='default'):
    record = {'id': f'pair-{seen:.0f}', 'room': 'r', 'secret': 's', 'workspace': workspace, 'remote_workspace': workspace,
              'peer': {'fingerprint': 'device-b', 'hostname': 'bedrock', 'user': 'u', 'os': 'linux'},
              'relay': 'wss://relay', 'direction': 'both', 'enabled': True, 'state': 'active',
              'created_at': time.time() - 3600, 'last_seen': time.time() - seen, 'last_sync': None, 'last_error': ''}
    sync_pairs.save(record)
    return record


def _earlier_claim(task: str, *, by='bedrock-agent') -> None:
    """Another device's claim on ``task``, made after the Task was created and
    before this device's claim, arriving by sync with its live lease."""
    events = tasks.history(task)
    created = next(e for e in events if e['kind'] == 'created')
    ours = [e for e in events if e['kind'] == 'claimed'][-1]
    first, last = (int(e['hlc'].split('.')[0]) for e in (created, ours))
    assert last - first >= 2, 'the claim came too soon after the Task to fit one between'
    at = (first + last) // 2
    claim_id = ('e' * 31 + '1')
    now = time.time()
    tasks.sync_import([
        {'type': 'event', 'id': claim_id, 'task': created['task'], 'kind': 'claimed',
         'hlc': f'{at:013d}.0000', 'device': 'device-b', 'actor': 'agent', 'session': 'conversation:b1',
         'agent': by, 'client': 'codex', 'recorded_at': at / 1000, 'data': {'token_hash': 'x' * 64}},
        {'type': 'lease', 'claim': claim_id, 'renewed_at': now, 'active_at': now, 'device': 'device-b'},
    ])


def _story(task: str, run: dict | None = None) -> str:
    """What the Board recorded for ``task`` and the end of the run's log: a
    failure's message, so a failure on a runner nobody can reach explains itself."""
    lines = []
    for event in tasks.history(task):
        data = event['data']
        lines.append(f"{event['hlc']} {event['kind']} by {event['actor']} {event['session']} ({event.get('client')}) "
                     f"id={event['id'][:8]} token={str(data.get('token_hash'))[:8]} "
                     f"replaces={str(data.get('replaces'))[:8]} claim={str(data.get('claim'))[:8]} "
                     f"status={data.get('status')}")
    state = tasks.get(task)
    lines.append(f"holder={state.get('holder')} stale={state.get('stale')} previous={state.get('previous_holder')}")
    if run:
        lines.append('log: ' + dispatched.log_tail(store.log_path(run['id'])))
    return '\n'.join(lines)


class SyncRequestTests(DispatchCase):
    def test_an_exchange_asked_for_is_due_until_one_begins_after_it(self):
        pair = _pair()
        asked = sync_pairs.request_exchange([pair['id']])
        record = sync_pairs.get(pair['id'])
        self.assertEqual(record['exchange_requested'], asked)
        self.assertTrue(sync_pairs._asked_now(record))
        sync_pairs.update(pair['id'], last_exchange_began=asked + 1)
        self.assertFalse(sync_pairs._asked_now(sync_pairs.get(pair['id'])))

    def test_an_old_request_is_not_honoured_so_an_offline_device_is_not_asked_in_a_loop(self):
        pair = _pair()
        sync_pairs.request_exchange([pair['id']])
        later = time.time() + sync_pairs.EXCHANGE_NOW_SECONDS + 1
        self.assertFalse(sync_pairs._asked_now(sync_pairs.get(pair['id']), now=later))


class BeforeStartingTests(DispatchCase):
    def test_without_recent_peers_nothing_is_waited_for(self):
        _pair(seen=3600)  # not seen for an hour
        begun = time.monotonic()
        run = dispatched.run(self.task)
        self.assertLess(time.monotonic() - begun, 30)
        self.assertEqual(run['outcome'], 'review')

    def test_the_client_starts_once_the_claim_has_reached_the_peer(self):
        pair = _pair(seen=30)

        def sync_worker():
            while not (sync_pairs.get(pair['id']) or {}).get('exchange_requested'):
                time.sleep(0.05)
            time.sleep(0.3)
            sync_pairs.update(pair['id'], last_exchange_began=time.time())
        threading.Thread(target=sync_worker, daemon=True).start()
        with patch.object(claimcheck, 'POLL', 0.05):
            run = dispatched.run(self.task)
        self.assertEqual(run['outcome'], 'review')

    def test_a_claim_that_lost_to_an_earlier_one_starts_nothing(self):
        pair = _pair(seen=30)

        def sync_worker():
            while not (sync_pairs.get(pair['id']) or {}).get('exchange_requested'):
                time.sleep(0.05)
            _earlier_claim(self.task)  # the exchange brings bedrock's earlier claim
            sync_pairs.update(pair['id'], last_exchange_began=time.time())
        threading.Thread(target=sync_worker, daemon=True).start()
        with patch.object(claimcheck, 'POLL', 0.05), self.assertRaises(dispatched.ClaimLost):
            dispatched.run(self.task)

        self.assertFalse(self.record.exists())  # the client never started
        run = store.recent(1)[0]
        self.assertEqual(run['outcome'], 'lost')
        self.assertIn('bedrock-agent (codex)', run['detail'])
        self.assertEqual(tasks.get(self.task)['holder']['name'], 'bedrock-agent')

    def test_waiting_ends_after_its_limit_when_the_peer_does_not_answer(self):
        _pair(seen=30)
        actor = tasks.agent('dispatch:waiting', client='claude-code')
        claimed_at = time.time()
        tasks.claim(self.task, actor=actor)
        begun = time.monotonic()
        with patch.object(claimcheck, 'POLL', 0.05):
            verdict, _ = claimcheck.confirm(self.task, 'default', 'dispatch:waiting', claimed_at, wait=0.3)
        self.assertLess(time.monotonic() - begun, 2)  # the peer never answered, and the wait still ended
        self.assertEqual(verdict, claimcheck.HELD)

    def test_a_claim_the_person_released_while_waiting_starts_nothing(self):
        pair = _pair(seen=30)

        def person():
            while not (sync_pairs.get(pair['id']) or {}).get('exchange_requested'):
                time.sleep(0.05)
            tasks.release(self.task, actor=tasks.USER)
            sync_pairs.update(pair['id'], last_exchange_began=time.time())
        threading.Thread(target=person, daemon=True).start()
        with patch.object(claimcheck, 'POLL', 0.05), self.assertRaises(dispatched.ClaimLost):
            dispatched.run(self.task)
        self.assertFalse(self.record.exists())
        run = store.recent(1)[0]
        self.assertEqual((run['outcome'], run['detail']), ('stopped', 'was not started: the person released its claim'))

    def test_a_pair_of_another_workspace_is_not_waited_for(self):
        _pair(seen=30, workspace='other')
        self.assertEqual(claimcheck.peers('default'), [])


class WhileRunningTests(DispatchCase):
    def test_a_running_session_whose_claim_is_lost_is_stopped_by_the_dispatcher(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}), patch.object(dispatched, 'LOST_CHECK', 0.1):
            run, proc = dispatched.start(self.task)
            session = dispatched.Session(run, proc)
            self.assertIsNone(session.step())
            _earlier_claim(self.task)
            deadline = time.monotonic() + 20
            while (ended := session.step()) is None and time.monotonic() < deadline:
                time.sleep(0.1)

        self.assertEqual(ended['outcome'], 'lost')
        self.assertIn('lost its claim to bedrock-agent (codex)', ended['detail'])
        self.assertIsNotNone(proc.poll())
        note = [e for e in tasks.history(self.task) if e['kind'] == 'comment'][-1]
        self.assertIn('stay on task/', note['data']['text'])
        self.assertEqual(tasks.get(self.task)['holder']['name'], 'bedrock-agent')  # theirs is untouched

    def _until_taken_over(self, session):
        took = Path(str(self.record) + '.took')
        deadline = time.monotonic() + 20
        while not took.exists() and time.monotonic() < deadline:
            ended = session.step()
            self.assertIsNone(ended, ended and _story(self.task, session.run))
            time.sleep(0.05)
        self.assertTrue(took.exists(), 'the fake session never took the claim over')

    def test_a_takeover_without_the_token_is_another_agents_and_stops_the_session(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'take-and-hang'}), patch.object(dispatched, 'LOST_CHECK', 0.1):
            run, proc = dispatched.start(self.task)
            session = dispatched.Session(run, proc)
            self._until_taken_over(session)
            self.assertTrue(tasks.get(self.task)['stale'])  # its server's process ended
            self.assertEqual(len(dispatched._our_sessions(self.task, run['id'], 'default')), 2)
            other = tasks.agent('conversation:interactive-2', client='codex')
            tasks.claim(self.task, actor=other)  # a Stale Task, taken over without the token
            self.assertNotIn('conversation:interactive-2', dispatched._our_sessions(self.task, run['id'], 'default'))
            deadline = time.monotonic() + 20
            while (ended := session.step()) is None and time.monotonic() < deadline:
                time.sleep(0.1)

        self.assertEqual(ended['outcome'], 'lost')
        self.assertIsNotNone(proc.poll())
        self.assertEqual(tasks.get(self.task)['holder']['session'], 'conversation:interactive-2')
        self.assertNotIn('released', [e['kind'] for e in tasks.history(self.task)])

    def test_a_takeover_with_the_token_while_the_check_reads_is_still_the_runs(self):
        # Seen on a slow runner: the client's takeover landed between the check's reads of the Board.
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}):
            run, proc = dispatched.start(self.task)
            self.addCleanup(lambda: (dispatched.kill(proc.pid, proc), proc.wait()))
            session = dispatched.Session(run, proc)
            deadline, token = time.monotonic() + 60, None
            while token is None and time.monotonic() < deadline:
                try:  # the fake may still be writing its record (seen on Windows)
                    token = re.search(r'- Your claim token: (\S+)', self.recorded()['prompt']).group(1)
                except (OSError, ValueError, KeyError, AttributeError):
                    time.sleep(0.05)
            self.assertIsNotNone(token, 'the fake session never recorded its prompt')
            client = tasks.agent('conversation:fake-client', client='claude-code')
            read, took = tasks.get, []

            def takeover_then_read(task, **kwargs):
                if not took and sys._getframe(1).f_globals['__name__'] == claimcheck.__name__:
                    took.append(tasks.claim(task, actor=client, token=token))
                return read(task, **kwargs)
            with patch.object(tasks, 'get', takeover_then_read):
                ended = session._claim_ended()

        self.assertTrue(took, 'the check never read the Task')
        self.assertIsNone(ended)
        self.assertIsNone(session.lost)

    def test_the_end_of_a_run_never_releases_another_agents_claim(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'take-and-hang'}):
            run, proc = dispatched.start(self.task)
            session = dispatched.Session(run, proc)
            self._until_taken_over(session)
            other = tasks.agent('conversation:interactive-2', client='codex')
            tasks.claim(self.task, actor=other)
            dispatched.kill(proc.pid, proc)
            ended = dispatched.finish(run['id'], proc.returncode)  # before any check saw the takeover

        self.assertEqual(ended['outcome'], 'failed')
        self.assertEqual(tasks.get(self.task)['holder']['session'], 'conversation:interactive-2')
        self.assertNotIn('released', [e['kind'] for e in tasks.history(self.task)])

    def test_a_lost_claim_is_no_failure(self):
        from imprint.dispatch import guards
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}), patch.object(dispatched, 'LOST_CHECK', 0.1):
            run, proc = dispatched.start(self.task)
            session = dispatched.Session(run, proc)
            _earlier_claim(self.task)
            deadline = time.monotonic() + 20
            while (ended := session.step()) is None and time.monotonic() < deadline:
                time.sleep(0.1)
        self.assertEqual(ended['outcome'], 'lost')
        self.assertEqual(guards.failures_in_a_row(), 0)
        self.assertNotIn('user', tasks.get(self.task)['assignees'])



class ClaimEndedTests(DispatchCase):
    """A running session whose claim ends without another claim winning (ADR 0225)."""

    def _run_until(self, mode, act):
        with patch.dict(os.environ, {'FAKE_MODE': mode}), patch.object(dispatched, 'LOST_CHECK', 0.1):
            run, proc = dispatched.start(self.task)
            session = dispatched.Session(run, proc)
            took = Path(str(self.record) + '.took')
            deadline = time.monotonic() + 20
            ended = None
            # The check may see the session's own change before its marker is written (slower on Windows).
            while mode != 'hang' and not took.exists() and ended is None and time.monotonic() < deadline:
                ended = session.step()
                time.sleep(0.05)
            if act:
                self.assertIsNone(ended, ended and 'the session ended before the test acted\n' + _story(self.task, run))
                act()
            while ended is None and time.monotonic() < deadline + 20:
                ended = session.step()
                time.sleep(0.1)
        self.assertIsNotNone(proc.poll(), 'the session was not stopped')
        return run, ended

    def test_a_session_that_releases_its_own_claim_has_failed_and_keeps_its_worktree(self):
        from imprint.dispatch import guards
        run, ended = self._run_until('release-and-hang', None)
        self.assertEqual((ended['outcome'], ended['detail']),
                         ('failed', 'released its claim without finishing and was stopped'), _story(self.task, run))
        self.assertEqual(guards.failures_in_a_row(), 1)
        self.assertTrue(Path(run['worktree']).exists())

    def test_the_persons_release_stops_the_session_without_a_failure(self):
        from imprint.dispatch import guards
        run, ended = self._run_until('hang', lambda: tasks.release(self.task, actor=tasks.USER))
        self.assertEqual((ended['outcome'], ended['detail']), ('stopped', 'was stopped: the person released its claim'))
        self.assertEqual(guards.failures_in_a_row(), 0)
        self.assertTrue(Path(run['worktree']).exists())

    def test_the_persons_cancel_stops_the_session(self):
        run, ended = self._run_until('take-and-hang', lambda: tasks.move(self.task, 'cancelled', actor=tasks.USER))
        self.assertEqual((ended['outcome'], ended['detail']), ('stopped', 'was stopped: the person cancelled the Task'),
                         _story(self.task, run))
        self.assertTrue(Path(run['worktree']).exists())  # cleanup removes it later, keeping the branch

    def test_the_persons_done_stops_the_session(self):
        run, ended = self._run_until('hang', lambda: tasks.move(self.task, 'done', actor=tasks.USER))
        self.assertEqual((ended['outcome'], ended['detail']), ('stopped', 'was stopped: the person moved the Task to Done'))

    def test_a_lost_claim_removes_the_worktree(self):
        run, ended = self._run_until('hang', lambda: _earlier_claim(self.task))
        self.assertEqual(ended['outcome'], 'lost')
        self.assertFalse(Path(run['worktree']).exists())


if __name__ == '__main__':
    import unittest
    unittest.main()
