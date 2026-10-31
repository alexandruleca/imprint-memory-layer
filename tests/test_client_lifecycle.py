"""Slow local model work must not expire a retained HTTP client."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from emulation import slack


class ClientLifecycleTests(unittest.TestCase):
    def test_slow_single_batch_and_query_embeddings_keep_the_client_open(self):
        # A fresh interpreter starts the actual legacy idle hook deterministically
        # without the parent test suite's already-imported MCP timer. Storage and
        # models are fake, but operations use production store/search code.
        source = textwrap.dedent('''
            import time
            from types import SimpleNamespace
            from unittest.mock import patch
            from imprint import vectorstore as vs
            class Client:
                closed = False
                writes = 0
                queries = 0
                def close(self): self.closed = True
                def upsert(self, **kwargs):
                    assert not self.closed, 'client closed during embedding'
                    self.writes += 1
                def get_collection(self, name): return SimpleNamespace(points_count=1)
                def scroll(self, **kwargs): return [], None  # no stored versions to adopt (ADR 0060)
                def query_points(self, **kwargs):
                    assert not self.closed, 'client closed during query embedding'
                    self.queries += 1
                    return SimpleNamespace(points=[])
            client = Client()
            vs._client = client
            vs.release_idle_client(after_seconds=.01)
            def embed(*args):
                time.sleep(1.2)
                return [0.1] * 1024
            def batch(texts): return [embed(text) for text in texts]
            with patch.object(vs, '_ensure_collection', return_value=(client,'fixture')), \\
                 patch.object(vs, '_live_existing_ids', return_value=set()), \\
                 patch.object(vs.embeddings, 'embed_document', side_effect=embed), \\
                 patch.object(vs.embeddings, 'embed_query', side_effect=embed), \\
                 patch.object(vs.embeddings, 'embed_documents_batch', side_effect=batch):
                vs.store('Durable single note', workspace='default')
                assert vs.store_batch([{'content':'Durable batch note'}], workspace='default') == (1,0)
                assert vs.search('Durable notes', mode='semantic', workspace='default') == []
            assert client.writes == 2 and client.queries == 1
            vs._close_client()
            assert client.closed and vs._client is None
        ''')
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'PYTHONPATH': str(root), 'IMPRINT_DATA_DIR': directory,
                   'IMPRINT_WORKSPACE': 'default', 'IMPRINT_ZERO_SHOT_TAGS': '0'}
            result = subprocess.run([sys.executable, '-c', source], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=slack(120))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
