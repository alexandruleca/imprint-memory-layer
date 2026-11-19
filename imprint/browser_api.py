"""Narrow local API for Chrome capture, including legacy paired clients."""
import json
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import browser_capture as browser, config, queue
from .http_security import loopback_address
from .workspace_policy import workspace_name

router = APIRouter(prefix='/api/browser')
_HEADERS = {'authorization', 'content-type', 'x-imprint-extension-id'}


async def boundary(request, call_next):
    """Called only after the API's existing peer and Host checks."""
    origin = request.headers.get('origin')
    local = request.url.path.startswith('/api/browser/local/')
    if local and (request.client is None or not loopback_address(request.client.host)):
        return JSONResponse({'detail': 'Browser discovery requires a loopback connection'}, status_code=403)
    cors = {}
    try:
        if request.method == 'OPTIONS':
            prefix = 'chrome-extension://'
            extension_id = origin[len(prefix):] if origin and origin.startswith(prefix) else ''
        else:
            extension_id = request.headers.get('x-imprint-extension-id', '')
        expected = browser.extension_origin(extension_id)
        if local and request.method == 'POST' and origin is None:
            raise ValueError('Capture submission requires a Chrome extension origin')
        if origin is not None and origin != expected:
            raise ValueError('Extension origin does not match extension ID')
        pairing = None if local else browser.grant(extension_id)
        if not local and not pairing:
            raise ValueError('Extension is not paired with this Imprint')
        if origin:
            cors = {'Access-Control-Allow-Origin': origin, 'Vary': 'Origin'}
        if request.method == 'OPTIONS':
            headers = {v.strip().lower() for v in request.headers.get('access-control-request-headers', '').split(',') if v.strip()}
            if request.headers.get('access-control-request-method') not in ('GET', 'POST') or not headers <= _HEADERS:
                raise ValueError('Unsupported browser preflight')
            return Response(status_code=204, headers={**cors,
                'Access-Control-Allow-Methods': 'GET, POST',
                'Access-Control-Allow-Headers': ', '.join(sorted(_HEADERS))})
        if pairing:
            authorization = request.headers.get('authorization', '')
            token = authorization.removeprefix('Bearer ')
            if (not authorization.startswith('Bearer ') or not 20 <= len(token) <= 128
                    or not secrets.compare_digest(browser.token_hash(token), pairing['token_hash'])):
                return JSONResponse({'detail': 'Invalid pairing token; pair again in extension settings'}, status_code=401, headers=cors)
            workspace_name(pairing['workspace'])
    except ValueError as error:
        return JSONResponse({'detail': str(error)}, status_code=403, headers=cors)
    request.state.browser_pairing = pairing
    request.state.browser_extension_id = extension_id
    response = await call_next(request)
    response.headers.update(cors)
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/status')
def status(request: Request):
    return {'workspace': request.state.browser_pairing['workspace'], 'max_capture_bytes': browser.MAX_REQUEST_BYTES}


@router.get('/local/status')
def local_status():
    return {'active': config.get_active_workspace(), 'workspaces': config.get_known_workspaces(),
            'max_capture_bytes': browser.MAX_REQUEST_BYTES}


@router.post('/local/captures', status_code=202)
@router.post('/captures', status_code=202)
async def capture(request: Request):
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        return JSONResponse({'detail': 'Expected application/json'}, status_code=415)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > browser.MAX_REQUEST_BYTES:
            return JSONResponse({'detail': 'Capture exceeds 1 MB'}, status_code=413)
    try:
        body = json.loads(raw)
        pairing = request.state.browser_pairing
        if not isinstance(body, dict):
            raise ValueError('Expected a capture object')
        if pairing:
            workspace = pairing['workspace']
            if body.get('workspace', workspace) != workspace:
                raise ValueError('Pairing is bound to a different workspace')
            content = {**browser.normalize_capture(body), '_browser_pairing_hash': pairing['token_hash']}
        else:
            if not isinstance(body.get('workspace'), str) or not body['workspace']:
                raise ValueError('Choose an existing workspace')
            workspace = workspace_name(body['workspace'])
            content = browser.normalize_url_ingest(body) if 'url' in body else browser.normalize_capture(body)
        job_id = queue.enqueue('browser-capture', {**content, 'workspace': workspace,
            '_browser_extension_id': request.state.browser_extension_id})
    except (ValueError, UnicodeError) as error:
        return JSONResponse({'detail': str(error)}, status_code=400)
    return {'job_id': job_id, 'status': 'queued', 'workspace': workspace}


@router.get('/local/jobs/{job_id}')
@router.get('/jobs/{job_id}')
def job_status(job_id: str, request: Request):
    queue._init_db()
    job = queue.get_job(job_id)
    pairing = request.state.browser_pairing
    body = job.get('body', {}) if job else {}
    if (not job or job['command'] != 'browser-capture'
            or body.get('_browser_extension_id') != request.state.browser_extension_id
            or body.get('_browser_pairing_hash') != (pairing['token_hash'] if pairing else None)
            or (pairing and body.get('_workspace') != pairing['workspace'])):
        return JSONResponse({'detail': 'Capture job not found'}, status_code=404)
    return {key: job[key] for key in ('id', 'status', 'exit_code', 'created_at', 'ended_at')}
