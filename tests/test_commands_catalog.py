"""The Commands page's catalog, the commands POST /api/commands accepts, and
the arguments each queued command reaches the CLI with."""

import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from imprint import commands, queue

REPO = Path(__file__).resolve().parents[1]
SAMPLES = {'switch': True, 'path': '/home/me/project', 'text': 'value', 'project': 'notes', 'client': 'cursor'}


def request(method, path, body=None):
    from imprint.api import app

    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420') as client:
            return await client.request(method, path, json=body)
    return asyncio.run(send())


class CatalogTests(unittest.TestCase):
    def test_every_field_reaches_the_cli(self):
        for command in commands.CATALOG:
            fixed = command.get('fixed', {})
            without = queue.build_command_args(command['name'], dict(fixed))
            for field in command['fields']:
                body = {**fixed, field['key']: SAMPLES[field['kind']]}
                self.assertNotEqual(queue.build_command_args(command['name'], body), without,
                                    f"{command['name']}.{field['key']} never reaches the CLI")

    def test_client_commands_run_the_cli_with_their_target(self):
        with patch.object(queue, 'find_imprint_binary', return_value='imprint'):
            self.assertEqual(queue._build_argv('connect', {'target': 'cursor'}), ['imprint', 'connect', 'cursor'])
            self.assertEqual(queue._build_argv('disconnect', {'target': 'all'}), ['imprint', 'disconnect', 'all'])
            self.assertEqual(queue._build_argv('doctor', {'probe_mcp': True}), ['imprint', 'doctor', '--probe-mcp'])
            self.assertEqual(queue._build_argv('learn', {'desktop': True}), ['imprint', 'learn', '--desktop'])
            self.assertEqual(queue._build_argv('learn', {}), ['imprint', 'learn'])
            self.assertEqual(queue._build_argv('wsl', {'action': 'setup', 'distribution': 'Ubuntu', 'target': 'cursor'}),
                             ['imprint', 'wsl', 'setup', '--distribution', 'Ubuntu', '--target', 'cursor'])
            self.assertEqual(queue._build_argv('wsl', {'action': 'remove', 'distribution': 'Debian 12', 'target': 'codex'}),
                             ['imprint', 'wsl', 'remove', '--distribution', 'Debian 12', '--target', 'codex'])

    def test_removal_asks_first(self):
        confirmed = {command['name'] for command in commands.CATALOG if command.get('confirm')}
        self.assertEqual(confirmed, {'disconnect'})


class AllowedCommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': temp.name, 'IMPRINT_WORKSPACE': ''})
        env.start()
        self.addCleanup(env.stop)
        notify = patch.object(queue, '_notify_dispatcher')
        notify.start()
        self.addCleanup(notify.stop)

    def test_the_catalog_and_dialog_flows_are_accepted(self):
        self.assertEqual(commands.ALLOWED,
                         {command['name'] for command in commands.CATALOG} | {'wipe', 'migrate', 'workspace', 'wsl'})
        response = request('POST', '/api/commands/connect', {'target': 'cursor'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(queue.get_job(response.json()['job_id'])['body']['target'], 'cursor')
        response = request('POST', '/api/commands/wsl', {'action': 'setup', 'distribution': 'Ubuntu', 'target': 'codex'})
        self.assertEqual(response.status_code, 200, response.text)

    def test_commands_that_replace_or_stop_the_dashboard_are_refused(self):
        for command in ('setup', 'enable', 'disable', 'bootstrap', 'profile', 'ui', 'server', 'uninstall', 'update'):
            response = request('POST', f'/api/commands/{command}', {})
            self.assertEqual(response.status_code, 404, command)
        self.assertEqual(queue.list_queue(recent_limit=0)['queued'], [])


class FakeCLITestCase(unittest.TestCase):
    """Runs `script` as the imprint CLI the API shells out to."""

    def fake_cli(self, script):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        if os.name == 'nt':
            cli = Path(directory.name, 'imprint.cmd')
            cli.write_text(f'@"{sys.executable}" -c {json.dumps(script)} %*\n')
        else:
            cli = Path(directory.name, 'imprint')
            cli.write_text(f'#!{sys.executable}\n{script}\n')
            cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
        return patch.object(queue, 'find_imprint_binary', return_value=str(cli))


class CatalogEndpointTests(FakeCLITestCase):
    def test_serves_the_catalog_with_the_clis_clients(self):
        listed = [{'name': 'cursor', 'label': 'Cursor', 'connected': True},
                  {'name': 'codex', 'label': 'Codex', 'connected': False}]
        script = f'import sys, json\nassert sys.argv[1:] == ["clients", "--json"], sys.argv\nprint(json.dumps({listed!r}))'
        with self.fake_cli(script):
            response = request('GET', '/api/commands')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['clients'], listed)
        self.assertEqual([command['name'] for command in response.json()['commands']],
                         [command['name'] for command in commands.CATALOG])

    def test_a_cli_that_cannot_list_clients_is_reported(self):
        with self.fake_cli('import sys\nprint("unknown command clients")\nsys.exit(1)'):
            response = request('GET', '/api/commands')
        self.assertEqual(response.status_code, 502)
        self.assertIn('unknown command clients', response.json()['error'])


class WSLProfilesEndpointTests(FakeCLITestCase):
    LISTING = {'targets': [{'name': 'claude-code', 'label': 'Claude Code'}, {'name': 'cursor', 'label': 'Cursor'}],
               'distributions': [{'distribution': 'Ubuntu', 'home': '/home/me', 'clients': [
                   {'name': 'claude-code', 'profile': '/home/me/.claude', 'enrolled': True},
                   {'name': 'cursor', 'profile': '', 'enrolled': False}]}]}

    def test_a_windows_runtime_serves_the_clis_listing(self):
        script = (f'import sys, json\nassert sys.argv[1:] == ["wsl", "list", "--json"], sys.argv\n'
                  f'print(json.dumps({self.LISTING!r}))')
        with self.fake_cli(script), patch.object(commands.sys, 'platform', 'win32'):
            response = request('GET', '/api/wsl')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'supported': True, **self.LISTING})

    def test_other_runtimes_say_so_without_probing(self):
        with self.fake_cli('raise SystemExit("probed")'), patch.object(commands.sys, 'platform', 'linux'):
            response = request('GET', '/api/wsl')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['supported'], False)

    def test_a_listing_failure_is_reported(self):
        with self.fake_cli('import sys\nprint("Run imprint wsl from the Windows installation")\nsys.exit(1)'), \
                patch.object(commands.sys, 'platform', 'win32'):
            response = request('GET', '/api/wsl')
        self.assertEqual(response.status_code, 502)
        self.assertIn('Run imprint wsl from the Windows installation', response.json()['error'])


# Runs one job through the real dispatcher while this process's standard
# input stays open: a job that reads it must get end-of-file, not wait.
STDIN_CHILD = textwrap.dedent('''
    import asyncio, json, sys
    from imprint import queue
    queue._build_argv = lambda command, body: [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"]
    job_id = queue.enqueue("status", {})
    asyncio.run(queue._run_job(queue.get_job(job_id)))
    print(json.dumps({"status": queue.get_job(job_id)["status"],
                      "log": (queue._logs_dir() / f"{job_id}.log").read_text(encoding="utf-8")}))
''')


class NonInteractiveJobTests(unittest.TestCase):
    def test_a_job_reading_standard_input_gets_end_of_file(self):
        with tempfile.TemporaryDirectory() as data:
            env = {**os.environ, 'PYTHONPATH': str(REPO), 'IMPRINT_DATA_DIR': data}
            child = subprocess.Popen([sys.executable, '-c', STDIN_CHILD], env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                child.kill()
                self.fail('the job waited on standard input')
            finally:
                child.stdin.close()
            out, err = child.stdout.read(), child.stderr.read()
            child.stdout.close()
            child.stderr.close()
            self.assertEqual(child.returncode, 0, err.decode('utf-8', 'replace')[-2000:])
            result = json.loads(out.decode('utf-8').strip().splitlines()[-1])
            self.assertEqual(result, {'status': 'done', 'log': "''\n"})


if __name__ == '__main__':
    unittest.main()
