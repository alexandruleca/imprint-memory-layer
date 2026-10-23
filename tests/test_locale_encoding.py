"""Text from child processes is decoded explicitly, never in the platform's
locale encoding: cp1252 on Windows, where dashboard ingests failed on the
first box-drawing character of the CLI header."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Runs one job through the real dispatcher. The job prints what the CLI
# prints: a box-drawing header and a non-ASCII path, as UTF-8 bytes. The
# script itself stays ASCII because its interpreter runs in an ASCII locale.
CHILD = textwrap.dedent(r'''
    import asyncio, json, sys
    from imprint import queue
    box = chr(0x2550) * 3  # the box-drawing characters of the CLI header
    printed = box + " Imprint Ingest " + box + "\nC:\\Users\\alexa\\Desktop\\" + chr(0xDC) + "bersicht.pdf\n"
    queue._build_argv = lambda command, body: [
        sys.executable, "-c", "import sys; sys.stdout.buffer.write(%s.encode('utf-8'))" % ascii(printed)]
    job_id = queue.enqueue("ingest", {"dir": "desktop"})
    asyncio.run(queue._run_job(queue.get_job(job_id)))
    job = queue.get_job(job_id)
    log = (queue._logs_dir() / f"{job_id}.log").read_text(encoding="utf-8")
    print(json.dumps({"status": job["status"], "error": job.get("error"), "log": log}))
''')


# Lists a git repository whose file name is not ASCII: git's names must be
# the ones the filesystem layer gives this process, so the indexer's walk
# finds them.
GIT_CHILD = textwrap.dedent(r'''
    import json, os, sys
    from imprint.cli_index import _git_tracked_files
    repo = sys.argv[1]
    files = _git_tracked_files(repo)
    on_disk = set(os.listdir(repo)) - {".git"}
    print(json.dumps({"listed": files is not None and on_disk <= files}))
''')


def ascii_locale(**extra: str) -> dict:
    """An ASCII default encoding, as cp1252 is on Windows: no UTF-8 mode and
    no C-locale coercion."""
    return {**os.environ, "PYTHONPATH": str(REPO), "LC_ALL": "C", "LANG": "C",
            "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", **extra}


class LocaleEncodingTests(unittest.TestCase):
    def test_dispatcher_reads_utf8_output_in_a_non_utf8_locale(self):
        with tempfile.TemporaryDirectory() as data:
            env = ascii_locale(IMPRINT_DATA_DIR=data)
            out = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr.decode("utf-8", "replace")[-2000:])
            result = json.loads(out.stdout.decode("ascii").strip().splitlines()[-1])
            self.assertEqual(result["status"], "done", result)
            self.assertIn("═══ Imprint Ingest ═══", result["log"])
            self.assertIn("Übersicht.pdf", result["log"])

    def test_git_listing_keeps_non_ascii_names_in_a_non_utf8_locale(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as repo:
            Path(repo, "\u00dcbersicht.md").write_text("notes")
            git = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
            subprocess.run(["git", "-C", repo, "init", "-q"], check=True, env=git)
            subprocess.run(["git", "-C", repo, "add", "."], check=True, env=git)
            env = ascii_locale(IMPRINT_DATA_DIR=data, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            out = subprocess.run([sys.executable, "-c", GIT_CHILD, repo], env=env, capture_output=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr.decode("utf-8", "replace")[-2000:])
            result = json.loads(out.stdout.decode("ascii").strip().splitlines()[-1])
            self.assertTrue(result["listed"], result)


if __name__ == "__main__":
    unittest.main()
