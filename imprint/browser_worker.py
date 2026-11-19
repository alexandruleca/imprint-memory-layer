"""Queue-owned capture worker; content stays out of process arguments/logs."""
import argparse
import hashlib
import secrets

from . import browser_capture as browser, queue, queue_lock
from .workspace_policy import workspace_name


def run(job_id):
    job = queue.get_job(job_id)
    if not job or job['command'] != 'browser-capture' or job['status'] != 'running':
        raise ValueError('Expected a running browser capture job')
    body = job['body']
    descriptor = queue_lock.acquire('browser-capture', job_id, block=True)
    if descriptor is None:
        raise RuntimeError('Could not acquire ingestion queue lock')
    try:
        workspace = workspace_name(body['_workspace'])
        if '_browser_pairing_hash' in body:
            pairing = browser.grant(body['_browser_extension_id'])
            if (not pairing or pairing['workspace'] != workspace
                    or not secrets.compare_digest(pairing['token_hash'], body['_browser_pairing_hash'])):
                raise ValueError('Browser pairing was revoked or changed before ingestion')
        if 'url' in body:
            from .cli_ingest_url import ingest_one
            from .vectorstore import get_url_sources

            target = browser.normalize_url_ingest(body)
            count, status = ingest_one(target['url'], target['project'], get_url_sources(workspace), workspace=workspace)
            if status not in ('stored', 'skipped-unchanged'):
                raise RuntimeError('URL ingestion did not complete: ' + status)
            print(f'URL ingestion complete: {count} new chunks', flush=True)
            return
        content = browser.normalize_capture(body)
        from .ingest_core import store_inline_content
        captured_at = job['created_at']
        for doc in content['documents']:
            metadata = {'title': doc['title'], 'capture_kind': doc['kind'],
                        'captured_at': captured_at, 'account': doc['account'],
                        'message_id': doc['message_id']}
            # Titles are indexed too; all metadata remains browser-supplied provenance.
            text = doc['title'] + '\n\n' + doc['text']
            count, status = store_inline_content(
                text, source_key=doc['source_key'], project=content['project'],
                source_type='browser', rel_path='browser-capture.txt', workspace=workspace,
                extra_fields={'source_url': doc['url'], 'doc_metadata': metadata,
                              'source_mtime': captured_at,
                              'source_hash': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                              'source_size': len(text.encode('utf-8'))})
            if status != 'stored':
                raise RuntimeError('Capture ingestion did not complete: ' + status)
            print(f'Indexed document: {count} new chunks', flush=True)
        print('Browser capture complete', flush=True)
    finally:
        queue_lock.release(descriptor)


def main():
    from ._cli_signals import install
    install()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id')
    args = parser.parse_args()
    run(args.job_id)


if __name__ == '__main__':
    main()
