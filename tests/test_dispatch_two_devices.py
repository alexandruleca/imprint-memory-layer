"""Two Dispatchers on two devices race for one Task (ADR 0224, T-mmafm).

Each device is a process of its own, with its own data dir, home and clone
of the project. This test is the relay between them: once both have
claimed the Task and asked for an exchange, it swaps their Boards and says
the exchange began, as a real relay would."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from imprint import device as this_device, sync_pairs, tasks, workspace_policy
from imprint.dispatch import store

from test_dispatch_run import FAKE_CLAUDE, REPO, _git, executable

DEVICE = '''
import json, os, sys, time
sys.path.insert(0, sys.argv[2])
from imprint.dispatch import claimcheck, dispatched
claimcheck.POLL = 0.05
try:
    run, proc = dispatched.start(sys.argv[1], client='claude-code')
except dispatched.ClaimLost as lost:
    print(json.dumps({'started': False, 'detail': str(lost)}))
    sys.exit(0)
deadline = time.monotonic() + 60
while not os.path.exists(os.environ['FAKE_RECORD']) and time.monotonic() < deadline:
    time.sleep(0.05)
dispatched.kill(proc.pid, proc)
dispatched.finish(run['id'], proc.returncode, reason='was stopped by the test', stopped=True)
print(json.dumps({'started': True, 'recorded': os.path.exists(os.environ['FAKE_RECORD'])}))
'''


class TwoDevicesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'bin').mkdir()
        fake = executable(root / 'bin' / 'claude', FAKE_CLAUDE.format(python=sys.executable, repo=str(REPO)))
        self.env = {}
        for device in ('a', 'b'):
            home = root / device / 'home'
            home.mkdir(parents=True)
            (home / '.claude.json').write_text(json.dumps({'mcpServers': {'imprint': {'command': 'imprint'}}}))
            self.env[device] = {'IMPRINT_DATA_DIR': str(root / device / 'data'), 'IMPRINT_WORKSPACE': 'default',
                                'IMPRINT_DISPATCH_CLAUDE_CODE_COMMAND': str(fake), 'HOME': str(home),
                                'USERPROFILE': str(home), 'FAKE_RECORD': str(root / device / 'record.json'),
                                'FAKE_MODE': 'hang'}
        repo = {'a': root / 'a' / 'code' / 'shop', 'b': root / 'b' / 'code' / 'shop'}
        repo['a'].mkdir(parents=True)
        _git(repo['a'], 'init', '-q', '-b', 'dev')
        (repo['a'] / 'README.md').write_text('shop\n')
        _git(repo['a'], 'add', 'README.md')
        _git(repo['a'], 'commit', '-q', '-m', 'first')
        _git(root, 'clone', '-q', str(repo['a']), str(repo['b']))  # each device its own clone
        for device in ('a', 'b'):
            with self._as(device):
                workspace_policy.map_project('shop', str(repo[device]))
                sync_pairs.save({'id': 'pair', 'room': 'r', 'secret': 's', 'workspace': 'default',
                                 'remote_workspace': 'default', 'relay': 'wss://relay', 'direction': 'both',
                                 'peer': {'fingerprint': f'device-{device}', 'hostname': 'other', 'user': 'u',
                                          'os': 'linux'},
                                 'enabled': True, 'state': 'active', 'created_at': time.time() - 3600,
                                 'last_seen': time.time(), 'last_sync': None, 'last_error': ''})
        with self._as('a'):
            self.task = tasks.create('Add a done file', project='shop', status='ready',
                                     acceptance=['done.txt exists'])['id']
        self._exchange()  # both devices know the Task before either claims it

    @contextmanager
    def _as(self, device: str):
        """Imprint calls in this process made as ``device``: only this thread switches."""
        with patch.dict(os.environ, self.env[device]):
            yield

    def _exchange(self) -> None:
        board = {}
        for device in ('a', 'b'):
            with self._as(device):
                board[device] = tasks.sync_export(list(tasks.sync_manifest()))
        for device, other in (('a', 'b'), ('b', 'a')):
            with self._as(device):
                tasks.sync_import(board[other])

    def _asked(self, device: str) -> bool:
        with self._as(device):
            return bool((sync_pairs.get('pair') or {}).get('exchange_requested'))

    def test_two_dispatchers_claiming_one_task_start_exactly_one_client(self):
        devices = {device: subprocess.Popen([sys.executable, '-c', DEVICE, self.task, str(REPO)],
                                            env={**os.environ, **self.env[device]}, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, text=True)
                   for device in ('a', 'b')}
        for proc in devices.values():
            self.addCleanup(lambda p=proc: p.poll() is not None or (p.kill(), p.wait()))
        exchanged, deadline = False, time.monotonic() + 120
        while any(proc.poll() is None for proc in devices.values()) and time.monotonic() < deadline:
            if exchanged or all(self._asked(device) for device in devices):
                self._exchange()  # both claimed before any exchange: the race ADR 0224 settles
                if not exchanged:
                    began = time.time()
                    for device in devices:
                        with self._as(device):
                            sync_pairs.update('pair', last_exchange_began=began)
                    exchanged = True
            time.sleep(0.1)
        said = {}
        for device, proc in devices.items():
            out, err = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, err)
            said[device] = json.loads(out.strip().splitlines()[-1])

        self.assertTrue(exchanged, 'the devices never both asked for an exchange')
        self.assertEqual(sorted(s['started'] for s in said.values()), [False, True], said)
        with self._as('a'):
            claims = [e for e in tasks.history(self.task) if e['kind'] == 'claimed']
        first = min(claims, key=lambda e: e['hlc'])
        winner = next(name for name in devices if said[name]['started'])
        loser = next(name for name in devices if not said[name]['started'])
        with self._as(winner):
            self.assertEqual(first["device"], this_device.current()["id"])  # the earlier claim won
        self.assertTrue(said[winner]['recorded'])  # its client ran
        self.assertFalse(Path(self.env[loser]['FAKE_RECORD']).exists())  # the other's never started
        self.assertIn('claimed first by', said[loser]['detail'])
        with self._as(loser):
            self.assertEqual(store.recent(1)[0]['outcome'], 'lost')


if __name__ == '__main__':
    unittest.main()
