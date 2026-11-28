"""The Dispatcher's limits are bounded settings the person changes in Settings, and apply on the next pass (T-pngn6)."""

import io
import json
import os
import time
from contextlib import redirect_stderr
from unittest.mock import patch

from imprint import config, config_schema, tasks
from imprint.dispatch import guards, status, store

from test_dispatch_loop import LoopCase


class BoundsTests(LoopCase):
    def test_out_of_bounds_and_non_whole_values_are_refused_with_the_bounds(self):
        for key, bad in (('dispatch.concurrency', '9'), ('dispatch.concurrency', '-1'), ('dispatch.concurrency', '1.5'),
                         ('dispatch.daily_limit', '501'), ('dispatch.session_minutes', '4'),
                         ('dispatch.failure_limit', '101')):
            with self.subTest(key=key, value=bad), self.assertRaisesRegex(ValueError, 'expected a whole number from'):
                config_schema.set_value(key, bad)
        self.assertEqual(config_schema.set_value('dispatch.concurrency', '8'), 8)
        self.assertEqual(config_schema.set_value('dispatch.daily_limit', '0'), 0)

    def test_a_value_an_earlier_release_stored_out_of_bounds_reads_as_the_default(self):
        path = config.get_data_dir() / 'config.json'
        path.write_text(json.dumps({'dispatch': {'concurrency': 40, 'daily_limit': 10000}}))
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(guards.limit('dispatch.concurrency'), 1)
            self.assertEqual(guards.limit('dispatch.daily_limit'), 10)
        self.assertIn('dispatch.concurrency = 1, its default', err.getvalue())


class NextPassTests(LoopCase):
    def test_a_higher_concurrency_starts_a_second_session_on_the_next_pass(self):
        tasks.create('Second', project='shop', status='ready', acceptance=['ok'])
        status.set_roles('default', {'work'})
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}):
            first = self.dispatcher.dispatch()
            self.assertIsNotNone(first)
            self.assertIsNone(self.dispatcher.dispatch())  # one at a time by default
            config_schema.set_value('dispatch.concurrency', '2')
            second = self.dispatcher.dispatch()
            self.assertIsNotNone(second)
            self.assertNotEqual(second['task'], first['task'])
            for run in (first, second):
                self.dispatcher.sessions[run['id']].stop('was stopped because the test ended')
            self.settle()

    def test_a_higher_daily_limit_lets_it_start_again_the_same_day(self):
        now = time.time()
        for number in range(2):
            store.add({'id': f'today{number}', 'workspace': 'default', 'task': self.task, 'title': 't',
                       'project': 'shop', 'client': 'claude-code', 'role': 'work', 'started_at': now,
                       'ended_at': now, 'outcome': 'review', 'pid': 4242})
        config_schema.set_value('dispatch.daily_limit', '2')
        status.set_roles('default', {'work'})
        self.assertIn('the daily limit', guards.held())
        self.assertIsNone(self.dispatcher.dispatch())
        config_schema.set_value('dispatch.daily_limit', '3')
        self.assertIsNone(guards.held())
        self.assertIsNotNone(self.dispatcher.dispatch())
        self.settle()

    def test_the_strip_shows_todays_count_against_the_limits(self):
        config_schema.set_value('dispatch.daily_limit', '25')
        config_schema.set_value('dispatch.concurrency', '2')
        now = time.time()
        store.add({'id': 'today', 'workspace': 'default', 'task': self.task, 'title': 't', 'project': 'shop',
                   'client': 'claude-code', 'role': 'work', 'started_at': now, 'ended_at': now, 'outcome': 'review',
                   'pid': 4242})
        self.assertEqual(status.status('default', projects=False)['limits'],
                         {'today': 1, 'daily': 25, 'concurrency': 2})
