"""Workspace-scoped indexed-source removal; never deletes original files."""
from . import config, vectorstore as vs, workspace_policy as policy
from qdrant_client import models as qm


def purge(target, *, workspace=None, kind='path', recursive=False, apply=False):
    ws = policy.workspace_name(workspace)
    if kind not in ('path', 'source'):
        raise ValueError('kind must be path or source')
    if kind == 'path':
        path = policy.canonical(target)
        recursive = recursive or path.is_dir()
        target = str(path)
    else:
        target = policy.source_key(target)
    client, collection = vs._get_client(), config.collection_name(ws)
    sources, point_ids = set(), []
    if client.collection_exists(collection):
        offset = None
        while True:
            points, offset = client.scroll(collection_name=collection, limit=256, offset=offset,
                                           with_payload=['source', 'source_path', 'project'], with_vectors=False)
            for point in points:
                payload = point.payload or {}
                value = payload.get('source_path' if kind == 'path' else 'source', '')
                if policy.matches(value, target, recursive, kind):
                    point_ids.append(point.id)
                    sources.add((payload.get('project', ''), payload.get('source', '')))
            if offset is None:
                break
    if apply:
        # Keep this exclusion even if deletion fails, so retry/refresh cannot
        # silently reintroduce the unwanted source. Failure still propagates.
        policy.exclude(target, kind, recursive, ws)
        for start in range(0, len(point_ids), 256):
            client.delete(collection_name=collection,
                          points_selector=qm.PointIdsList(points=point_ids[start:start + 256]), wait=True)
        from . import imprint_graph as kg
        conn = kg._get_conn(ws)
        with conn:
            for _, source in sources:
                if source:
                    conn.execute('DELETE FROM facts WHERE source=?', (source,))
        vs._wal_log('purge', workspace=ws, target=target, kind=kind, recursive=recursive, chunks=len(point_ids))
        with vs._cache_lock:
            vs._id_caches.pop(collection, None)
    return {'workspace': ws, 'target': target, 'kind': kind, 'recursive': recursive,
            'chunks': len(point_ids), 'sources': len(sources), 'applied': apply,
            'sample': [{'project': project, 'source': source} for project, source in sorted(sources)[:20]]}


def run_purge(target, **options):
    """API/MCP entry point shares the CLI ingestion lock."""
    from . import queue_lock
    fd = queue_lock.acquire('purge', '', block=False)
    if fd is None:
        raise ValueError('An ingestion or maintenance job is running; retry purge after it finishes')
    try:
        return purge(target, **options)
    finally:
        queue_lock.release(fd)
