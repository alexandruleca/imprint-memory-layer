"""Workspace-scoped indexed-source removal; never deletes original files."""
from pathlib import Path
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
    if apply:
        # Exclude before enumerating: final storage upserts hold the same
        # policy transaction, so in-flight inference cannot restore this source.
        policy.exclude(target, kind, recursive, ws)
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
    from . import imprint_graph as kg
    conn, fact_ids = None, []
    if config.graph_db_path(ws).exists():
        conn = kg._get_conn(ws)
        matched_sources = {source for _, source in sources if source}
        for fact_id, source in conn.execute('SELECT id, source FROM facts'):
            if source and (source in matched_sources or
                           (kind == 'source' and policy.matches(source, target, recursive, kind)) or
                           (kind == 'path' and Path(source).is_absolute() and policy.matches(source, target, recursive, kind))):
                fact_ids.append(fact_id)
    if apply:
        # Keep this exclusion even if deletion fails, so retry/refresh cannot
        # silently reintroduce the unwanted source. Failure still propagates.
        if conn is not None:
            with conn:
                conn.executemany('DELETE FROM facts WHERE id=?', [(fact_id,) for fact_id in fact_ids])
        # Clear graph facts before their vector provenance disappears, so a
        # partial failure can be retried using the same physical-path target.
        for start in range(0, len(point_ids), 256):
            client.delete(collection_name=collection,
                          points_selector=qm.PointIdsList(points=point_ids[start:start + 256]), wait=True)
        vs._wal_log('purge', workspace=ws, target=target, kind=kind, recursive=recursive, chunks=len(point_ids))
        with vs._cache_lock:
            vs._id_caches.pop(collection, None)
    return {'workspace': ws, 'target': target, 'kind': kind, 'recursive': recursive,
            'chunks': len(point_ids), 'sources': len(sources), 'facts': len(fact_ids), 'applied': apply,
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
