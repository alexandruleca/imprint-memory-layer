#!/usr/bin/env python3
"""Exercise stdio MCP discovery and dashboard serving.

Run with the provisioned Python and PYTHONPATH pointing at the staged release.
No host configuration is changed. Use --with-storage for Qdrant write/read or
--with-embeddings for default-model semantic store/search.
"""
import asyncio
import gc
import os
import socket
import sys
import tempfile
from pathlib import Path


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    with tempfile.TemporaryDirectory(prefix='imprint-smoke-') as directory:
        os.environ['IMPRINT_DATA_DIR'] = directory
        os.environ['IMPRINT_QDRANT_PORT'] = str(free_port())
        os.environ['IMPRINT_QDRANT_GRPC_PORT'] = str(free_port())
        from fastapi.testclient import TestClient
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        import imprint
        from imprint.api import app, mount_static

        async def discover():
            transport = StdioTransport(command=sys.executable, args=['-m', 'imprint'],
                                       env=os.environ.copy(),
                                       cwd=str(Path(imprint.__file__).resolve().parent.parent),
                                       log_file=Path(directory) / 'mcp.log')
            async with Client(transport) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                names = set(tools)
                assert {'search', 'store', 'get_memory', 'status', 'ingest_content'}.issubset(names), names
                assert tools['search'].annotations.read_only_hint is True
                assert tools['store'].annotations.read_only_hint is False
                assert tools['search'].input_schema['properties']['query']['maxLength'] == 4000
                print(f'Stdio MCP handshake and discovery passed ({len(names)} tools)')
        asyncio.run(discover())
        mount_static()
        with TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 50000)) as client:
            assert client.get('/api/ping').status_code == 200
            page = client.get('/')
            assert page.status_code == 200 and '<html' in page.text
            assert client.get('/api/ping', headers={'Origin': 'https://example.com'}).status_code == 403
            assert client.get('/api/ping', headers={'Origin': 'http://localhost:8420'}).status_code == 200
            print('API health, static dashboard and origin boundary passed')
        if '--with-storage' in sys.argv or '--with-embeddings' in sys.argv:
            from imprint import qdrant_runner
            from qdrant_client import QdrantClient, models
            try:
                qdrant_runner.ensure_running()
                store = QdrantClient(host='127.0.0.1', port=int(os.environ['IMPRINT_QDRANT_PORT']))
                store.create_collection('smoke', vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
                store.upsert('smoke', points=[models.PointStruct(id=1, vector=[1., 0., 0.], payload={'text': 'smoke'})], wait=True)
                assert store.retrieve('smoke', ids=[1])[0].payload['text'] == 'smoke'
                store.delete_collection('smoke')
                store.close()
                print('Native Qdrant download, startup and write/read passed')
                if '--with-embeddings' in sys.argv:
                    from imprint import vectorstore
                    memory_id = vectorstore.store('Use native CI runners to validate desktop installers.', project='smoke')
                    hits = vectorstore.search('How should desktop installers be validated?', project='smoke')
                    assert hits and hits[0]['id'] == memory_id, hits
                    exact_id = vectorstore.store('def rotate_refresh_token_v2(): return create_rotated_token()',
                                                 project='smoke', source='smoke/tokens.py')
                    vectorstore.store('rotate_refresh_token_v2 belongs to another project.', project='other')
                    hits = vectorstore.search('rotate_refresh_token_v2', project='smoke', source='smoke/tokens.py')
                    assert hits and hits[0]['id'] == exact_id and hits[0]['lexical_score'] > 0, hits
                    from imprint.cli_viz import get_memory
                    assert get_memory(exact_id)['source'] == 'smoke/tokens.py'
                    from imprint import config
                    config.register_workspace('alternate')
                    assert vectorstore.search('rotate_refresh_token_v2', workspace='alternate') == []
                    print('Default ONNX semantic/hybrid retrieval, exact identifiers, filters, IDs and workspace isolation passed')
            finally:
                from imprint import vectorstore, embeddings
                vectorstore._close_client()
                embeddings._session = None
                embeddings._cpu_session = None
                embeddings._tokenizer = None
                gc.collect()  # Release model file mappings before Windows cleanup.
                qdrant_runner.stop()


if __name__ == '__main__':
    main()
