"""Exercise the actual fast-forward script against disposable repositories."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/sync-dev.sh'

@unittest.skipUnless(os.name == 'posix' and shutil.which('git') and shutil.which('bash'), 'POSIX workflow runner with git/bash required')
class DevSyncTests(unittest.TestCase):
    def test_fast_forward_and_divergence_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, work = root / 'remote.git', root / 'work'
            def git(*args, cwd=work):
                return subprocess.check_output(['git', *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
            git('init', '--bare', str(remote), cwd=root)
            git('clone', str(remote), str(work), cwd=root)
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            git('checkout', '-b', 'main')
            git('commit', '--allow-empty', '-m', 'base')
            git('push', 'origin', 'HEAD:main', 'HEAD:dev')
            git('commit', '--allow-empty', '-m', 'main change')
            git('push', 'origin', 'main')
            result = subprocess.run(['bash', str(SCRIPT)], cwd=work, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            main = git('rev-parse', 'origin/main')
            self.assertEqual(main, git('rev-parse', 'origin/dev'))
            git('checkout', '-b', 'dev', 'origin/dev')
            git('commit', '--allow-empty', '-m', 'dev-only work')
            git('push', 'origin', 'dev')
            dev = git('rev-parse', 'HEAD')
            git('checkout', 'main')
            git('commit', '--allow-empty', '-m', 'different main change')
            git('push', 'origin', 'main')
            result = subprocess.run(['bash', str(SCRIPT)], cwd=work, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('dev has commits outside main', result.stderr)
            self.assertEqual(dev, git('rev-parse', 'origin/dev'))
