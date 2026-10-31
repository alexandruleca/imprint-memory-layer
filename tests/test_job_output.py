"""A job's log shows its output as it is printed, not when the job ends."""

import asyncio
import os
import sys
import textwrap
import unittest
from unittest.mock import patch

from imprint import queue
from test_queue_priority import QueueCase
from emulation import slack, until


class JobOutputTests(QueueCase):
    def test_python_output_reaches_the_log_while_the_job_runs(self):
        # Into a pipe, Python holds stdout back in blocks unless the job's
        # environment says otherwise; an ingest showed no line for minutes.
        seen = self.root / 'seen'
        child = textwrap.dedent(f'''
            import os, time
            print("Scanning")
            deadline = time.monotonic() + {slack(20)}  # longer than the test waits for this line
            while not os.path.exists({str(seen)!r}) and time.monotonic() < deadline:
                time.sleep(0.05)
        ''')

        async def scenario():
            job_id = queue.enqueue('status', {})
            run = asyncio.create_task(queue._run_job(queue.get_job(job_id)))
            log = queue._logs_dir() / f'{job_id}.log'

            def printed():
                return log.exists() and 'Scanning' in log.read_text(encoding='utf-8')
            if await until(lambda: run.done() or printed(), 15, every=.05) and not run.done():
                seen.touch()
            while_running = seen.exists()
            await run
            return while_running

        with patch.dict(os.environ), \
                patch.object(queue, '_build_argv', return_value=[sys.executable, '-c', child]):
            os.environ.pop('PYTHONUNBUFFERED', None)
            self.assertTrue(asyncio.run(scenario()))


if __name__ == '__main__':
    unittest.main()
