"""Narrow authenticated API for the WIP Chrome capture extension."""
import json
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import browser_capture as browser, queue
from .workspace_policy import workspace_name

router = APIRouter(prefix='/api/browser')
_HEADERS = {'authorization', 'content-type', 'x-imprint-extension-id'}


async def boundary(request, call_next):
    """Called only after the API's existing peer and Host checks."""
    origin = request.headers.get('origin')
    cors = {}
    try:
        if request.method == 'OPTIONS':
            prefix = 'chrome-extension://'
            extension_id = origin[len(prefix):] if origin and origin.startswith(prefix) else ''
        else:
            extension_id = request.headers.get('x-imprint-extension-id', '')
        expected = browser.extension_origin(extension_id)
        pairing = browser.grant(extension_id)
        if origin is not None and origin != expected:
            raise ValueError('Extension origin does not match pairing')
        if not pairing:
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
        authorization = request.headers.get('authorization', '')
        token = authorization.removeprefix('Bearer ')
        if (not authorization.startswith('Bearer ') or not 20 <= len(token) <= 128
                or not secrets.compare_digest(browser.token_hash(token), pairing['token_hash'])):
            return JSONResponse({'detail': 'Invalid pairing token; pair again in extension settings'}, status_code=401, headers=cors)
        workspace_name(pairing['workspace'])
    except ValueError as error:
        return JSONResponse({'detail': str(error)}, status_code=403, headers=cors)
    request.state.browser_pairing = pairing
    response = await call_next(request)
    response.headers.update(cors)
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/status')
def status(request: Request):
    return {'workspace': request.state.browser_pairing['workspace'], 'max_capture_bytes': browser.MAX_REQUEST_BYTES}


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
        content = browser.normalize_capture(body)
        pairing = request.state.browser_pairing
        if body.get('workspace', pairing['workspace']) != pairing['workspace']:
            raise ValueError('Pairing is bound to a different workspace')
        job_id = queue.enqueue('browser-capture', {**content, 'workspace': pairing['workspace'],
            '_browser_extension_id': pairing['extension_id'], '_browser_pairing_hash': pairing['token_hash']})
    except (ValueError, UnicodeError) as error:
        return JSONResponse({'detail': str(error)}, status_code=400)
    return {'job_id': job_id, 'status': 'queued', 'workspace': pairing['workspace']}


@router.get('/jobs/{job_id}')
def job_status(job_id: str, request: Request):
    queue._init_db()
    job = queue.get_job(job_id)
    pairing = request.state.browser_pairing
    body = job.get('body', {}) if job else {}
    if (not job or job['command'] != 'browser-capture'
            or body.get('_browser_extension_id') != pairing['extension_id']
            or body.get('_browser_pairing_hash') != pairing['token_hash']
            or body.get('_workspace') != pairing['workspace']):
        return JSONResponse({'detail': 'Capture job not found'}, status_code=404)
    return {key: job[key] for key in ('id', 'status', 'exit_code', 'created_at', 'ended_at')}
