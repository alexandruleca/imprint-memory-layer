"""Paired devices send each other only what the other lacks, in chunks that
are stored as they arrive (ADR 0116).

Each side lists what sync can change, per record, without reading vectors:
its manifest. The devices swap manifests, and each sends the records the
other does not have or has an older version of. The records travel as
complete small streams, each stored and acknowledged before the next, so an
interrupted sync keeps what arrived and the next one sends only the rest.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import zlib

from . import config

# Advertised in the paired exchange; both devices must speak it.
VERSION = 1
# Memories (with their vectors, roughly 20 kB each) or facts per chunk.
CHUNK_RECORDS = 200
# How long either side waits for the other's next frame: while it builds its
# manifest, reads a chunk or stores one.
WAIT = 600
DATASETS = ('memories', 'facts', 'sources')


def offered() -> int | None:
    """The version this device offers in the paired exchange."""
    return VERSION


def storing_offered() -> bool:
    """Whether this device, sending, takes ``storing`` frames while it waits
    for an acknowledgement. 0.36.0 refused them, so a device sends them only
    to one that offers this."""
    return True
MANIFEST_BYTES = 256 * 1024 * 1024


def _fact_key(row) -> str:
    return json.dumps([row['subject'], row['predicate'], row['object'], row['valid_from']], ensure_ascii=False)


def _source_key(meta: dict) -> str:
    return json.dumps([meta['project'], meta['source']], ensure_ascii=False)


def manifest(workspace: str) -> dict[str, dict[str, float]]:
    """Every record sync can change, by key, with the version the importer
    compares: a memory's classification stamp (ADR 0051), a snapshot's update
    time. Facts only exist or not. Fields the importer never overwrites are
    left out, so records that differ only in those are not sent again and
    again. Reads no vectors."""
    from . import source_snapshots
    from . import vectorstore as vs

    memories = {pl['_mid']: float(pl.get('tagged_at') or 0)
                for pl in vs._scroll_all(['_mid', 'tagged_at'], workspace=workspace) if pl.get('_mid')}
    facts: dict[str, float] = {}
    try:
        from . import imprint_graph as kg
    except Exception:
        kg = None
    if kg is not None:
        with kg._connection(workspace) as conn:
            for row in conn.execute('SELECT subject, predicate, object, valid_from FROM facts'):
                facts[_fact_key(row)] = 0.0
    sources = {_source_key(meta): float(meta['updated_at']) for meta in source_snapshots.list_metadata(workspace)}
    return {'memories': memories, 'facts': facts, 'sources': sources}


def digest(local: dict) -> str:
    """One hash of a manifest: equal when nothing sync can change differs."""
    h = hashlib.sha256()
    for dataset in DATASETS:
        entries = local.get(dataset, {})
        for key in sorted(entries):
            h.update(json.dumps([dataset, key, entries[key]]).encode())
    return h.hexdigest()


def wanted(local: dict, remote: dict) -> dict[str, list[str]]:
    """The keys to send: held here, and missing there or older there."""
    return {dataset: sorted(key for key, version in local.get(dataset, {}).items()
                            if key not in remote.get(dataset, {}) or version > remote[dataset][key])
            for dataset in DATASETS}


def encode(local: dict) -> str:
    return base64.b64encode(zlib.compress(json.dumps(local, separators=(',', ':')).encode(), 6)).decode('ascii')


def decode(data: object) -> dict[str, dict[str, float]]:
    """A peer's manifest, or ValueError."""
    if not isinstance(data, str):
        raise ValueError('The other device sent a malformed manifest')
    inflate = zlib.decompressobj()
    try:
        raw = inflate.decompress(base64.b64decode(data, validate=True), MANIFEST_BYTES)
    except (ValueError, zlib.error):
        raise ValueError('The other device sent a malformed manifest') from None
    if inflate.unconsumed_tail:
        raise ValueError('The other device sent an oversized manifest')
    try:
        remote = json.loads(raw)
    except ValueError:
        raise ValueError('The other device sent a malformed manifest') from None
    if not isinstance(remote, dict) or not all(
            isinstance(remote.get(dataset, {}), dict)
            and all(isinstance(k, str) and type(v) in (int, float) for k, v in remote.get(dataset, {}).items())
            for dataset in DATASETS):
        raise ValueError('The other device sent a malformed manifest')
    return {dataset: remote.get(dataset, {}) for dataset in DATASETS}


def _stream(memories=(), facts=(), snapshot=()) -> list[str]:
    """A complete small stream, the format a full sync sends."""
    frames = [{'kind': 'meta', 'datasets': {'memories': len(memories), 'facts': len(facts)}, 'vectors': True}]
    if memories:
        frames.append({'kind': 'batch', 'dataset': 'memories', 'seq': 1, 'records': list(memories)})
    if facts:
        frames.append({'kind': 'batch', 'dataset': 'facts', 'seq': 1, 'records': list(facts)})
    frames.extend(snapshot)
    frames.append({'kind': 'done'})
    return [json.dumps(frame, separators=(',', ':')) for frame in frames]


def export_chunks(workspace: str, keys: dict[str, list[str]]):
    """The wanted records as small streams: memories and facts by
    ``CHUNK_RECORDS``, each source snapshot on its own."""
    from . import source_snapshots, sync_ws
    from . import vectorstore as vs
    from .device import TRANSFER_FIELDS

    identity = config.embedding_identity()
    mids = keys.get('memories', [])
    for start in range(0, len(mids), CHUNK_RECORDS):
        records = [sync_ws.memory_record(pl, identity) for pl in vs.retrieve_payloads(
            mids[start:start + CHUNK_RECORDS], sync_ws.MEMORY_FIELDS + TRANSFER_FIELDS, workspace, with_vectors=True)]
        if records:
            # A GPU here runs the Privacy Filter for the other device (ADR 0122).
            yield _stream(memories=vs.filtered_for_peer(records))

    wanted_facts = set(keys.get('facts', []))
    if wanted_facts:
        from . import imprint_graph as kg
        with kg._connection(workspace) as conn:
            rows = conn.execute('SELECT subject, predicate, object, valid_from, ended, source, recorded_device '
                                'FROM facts').fetchall()
        facts = [{'subject': r['subject'], 'predicate': r['predicate'], 'object': r['object'],
                  'valid_from': r['valid_from'], 'ended': r['ended'], 'source': r['source'] or '',
                  'recorded_device': json.loads(r['recorded_device'] or '{}')}
                 for r in rows if _fact_key(r) in wanted_facts]
        for start in range(0, len(facts), CHUNK_RECORDS):
            yield _stream(facts=facts[start:start + CHUNK_RECORDS])

    wanted_sources = set(keys.get('sources', []))
    for meta in source_snapshots.list_metadata(workspace):
        if _source_key(meta) in wanted_sources:
            yield _stream(snapshot=list(source_snapshots.export_parts(workspace, [meta])))


async def reconcile(channel, workspace: str, local: dict, *, provider: bool, provider_sends: bool,
                    consumer_sends: bool, cancel: asyncio.Event, result: dict, storing: bool = False):
    """Swap manifests, then send what the other device lacks: the provider's
    records first, then the consumer's. Yields ``progress`` events, and a
    ``storing`` status while either side stores a chunk; sets
    ``result['received']`` to what this side stored. ``storing``: the other
    device takes ``storing`` frames (``storing_offered``)."""
    if provider:
        await channel.send({'kind': 'manifest', 'data': encode(local)})
        remote = decode((await channel.recv(timeout=WAIT)).get('data'))
    else:
        remote = decode((await channel.recv(timeout=WAIT)).get('data'))
        await channel.send({'kind': 'manifest', 'data': encode(local)})
    for sender_is_provider, active in ((True, provider_sends), (False, consumer_sends)):
        if not active:
            continue
        if sender_is_provider == provider:
            async for event in _send(channel, workspace, wanted(local, remote), cancel):
                yield event
        else:
            async for event in _receive(channel, workspace, wanted(remote, local), cancel, result, storing):
                yield event


async def _send(channel, workspace: str, keys: dict, cancel: asyncio.Event):
    from .sync_ws import _check_cancel, _printable
    totals = {dataset: len(keys[dataset]) for dataset in ('memories', 'facts')}
    sent = dict.fromkeys(totals, 0)
    chunks = export_chunks(workspace, keys)
    seq = 0
    while True:
        _check_cancel(cancel)
        lines = await asyncio.to_thread(next, chunks, None)
        if lines is None:
            break
        seq += 1
        for line in lines:
            await channel.send_line(line)
        # Storing a chunk can outlast WAIT (Privacy Filter runs on every
        # record); the other device says it is still at it.
        ack = await channel.recv(timeout=WAIT)
        while ack.get('kind') == 'storing' and ack.get('seq') == seq:
            yield {'type': 'status', 'status': 'storing'}
            ack = await channel.recv(timeout=WAIT)
        if ack.get('kind') != 'ack' or ack.get('seq') != seq or ack.get('error'):
            raise RuntimeError(f"The other device did not store the sync: {_printable(ack.get('error') or 'no acknowledgement')}")
        for dataset, count in json.loads(lines[0])['datasets'].items():
            if count:
                sent[dataset] += count
                yield {'type': 'progress', 'phase': 'send', 'dataset': dataset, 'done': sent[dataset], 'total': totals[dataset]}
    await channel.send({'kind': 'end', 'chunks': seq})


async def _receive(channel, workspace: str, keys: dict, cancel: asyncio.Event, result: dict, storing: bool = False):
    from .sync_ws import _check_cancel, _printable, _receive_stream, _store, _temp_buffer
    totals = {dataset: len(keys[dataset]) for dataset in ('memories', 'facts')}
    stats = {'memories': {'inserted': 0, 'updated': 0, 'skipped': 0}, 'facts': {'inserted': 0, 'skipped': 0}}
    received = dict.fromkeys(totals, 0)
    seq = 0
    while True:
        _check_cancel(cancel)
        first = await channel.recv(timeout=WAIT)
        if first.get('kind') == 'end':
            if first.get('chunks') != seq:
                raise RuntimeError('The other device ended the sync before sending everything')
            break
        if first.get('kind') == 'error':
            raise RuntimeError(_printable(first.get('message', 'the other device could not export its memories')))
        seq += 1
        fd, path = _temp_buffer()
        try:
            with os.fdopen(fd, 'w') as out:
                async for _ in _receive_stream(channel, out, 'receive', cancel, first=first):
                    pass
            stored: dict = {}
            yield {'type': 'status', 'status': 'storing'}
            try:
                async for event in _store(path, workspace, 'store', cancel, stored):
                    if event['type'] == 'status':
                        if storing:
                            await channel.send({'kind': 'storing', 'seq': seq})
                        yield event
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await channel.send({'kind': 'ack', 'seq': seq, 'error': 'the other device could not store the sync'})
                raise RuntimeError(f'Storing failed: {e}') from None
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        for dataset, counts in stored['stats'].items():
            if dataset in stats:
                for key, value in counts.items():
                    stats[dataset][key] = stats[dataset].get(key, 0) + value
        if 'sources' in stored['stats']:
            stats['sources'] = {'imported': stats.get('sources', {}).get('imported', 0)
                                + stored['stats']['sources']['imported']}
        await channel.send({'kind': 'ack', 'seq': seq})
        result['received'] = stats
        for dataset in totals:
            done = sum(stats[dataset].values())
            if done != received[dataset]:
                received[dataset] = done
                yield {'type': 'progress', 'phase': 'receive', 'dataset': dataset, 'done': done, 'total': totals[dataset]}
    result['received'] = stats
