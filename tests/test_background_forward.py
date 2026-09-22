import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from utils import background_upload as bg
from utils.upload_state import Upload

class ForwardTests(unittest.IsolatedAsyncioTestCase):
    async def forward(self, status):
        upload = Upload('test.mp4', 7, received=True, options={'language': 'English', 'output_format': 'Subtitles', 'speakers': 2, 'verbatim': False})
        storage = {}
        with tempfile.NamedTemporaryFile(delete=False) as stream:
            stream.write(b'example')
            path = stream.name
        client_class = httpx.AsyncClient
        async def handle(request):
            self.assertEqual(request.headers['Authorization'], 'Bearer fixture')
            self.assertEqual(request.headers['X-Upload-Id'], upload.id.removeprefix('upload:'))
            if request.url.path.endswith('/fail'):
                return httpx.Response(404)
            if request.method == 'PUT':
                self.assertFalse(Path(path).exists(), 'Delete plaintext before queuing')
                self.assertEqual(json.loads(await request.aread()), dict(language='English', speakers=2, output_format='SRT', encryption_password=''))
                return httpx.Response(200, json={'result': {'status': 'pending'}})
            self.assertEqual(await request.aread(), b'example')
            return httpx.Response(status, json={'result': {'uuid': 'backend-id'}})
        def client(**kwargs):
            return client_class(transport=httpx.MockTransport(handle), **kwargs)
        with patch.object(bg.settings, 'API_URL', 'https://test.invalid'), patch.object(bg.httpx, 'AsyncClient', client):
            await bg.forward_file(upload, path, {'Authorization': 'Bearer fixture'}, storage)
        self.assertEqual(storage, {})
        self.assertFalse(Path(path).exists())
        return upload

    async def test_success_uses_captured_credentials_and_removes_plaintext(self):
        upload = await self.forward(200)
        self.assertEqual(upload.backend_id, 'backend-id')
        self.assertEqual(upload.phase, 'Queued')

    async def test_failure_is_visible_and_removes_plaintext(self):
        upload = await self.forward(503)
        self.assertEqual(upload.phase, 'Failed')
        self.assertTrue(upload.error)
        self.assertIsNone(upload.backend_id)

    async def test_submission_retries_same_job_after_lost_response(self):
        upload = Upload('x', 1, backend_id='existing', options=dict(language='Norwegian', output_format='Transcript', speakers=0, verbatim=True))
        requests = []
        client_class = httpx.AsyncClient
        async def handle(request):
            requests.append(request)
            if len(requests) == 1:
                raise httpx.ReadTimeout('response lost', request=request)
            return httpx.Response(200, json={'result': {'status': 'in_progress'}})
        with patch.object(bg.settings, 'API_URL', 'https://test.invalid'), patch.object(bg.httpx, 'AsyncClient', lambda **kw: client_class(transport=httpx.MockTransport(handle), **kw)):
            await bg.submit_upload(upload, {'Authorization': 'Bearer old'}, {'token': 'fresh'})
        self.assertEqual(upload.phase, 'Queued')
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].url, requests[1].url)
        self.assertEqual(requests[0].headers['x-upload-id'], requests[1].headers['x-upload-id'])
        self.assertEqual(requests[1].headers['authorization'], 'Bearer fresh')
        self.assertEqual(json.loads(requests[1].content)['language'], 'Norwegian (verbatim)')

    async def test_refusal_marks_overall_failure(self):
        upload = Upload('x', 1, backend_id='existing', options=dict(language='English', output_format='Transcript', speakers=0, verbatim=False))
        client_class = httpx.AsyncClient
        with patch.object(bg.settings, 'API_URL', 'https://test.invalid'), patch.object(bg.httpx, 'AsyncClient', lambda **kw: client_class(transport=httpx.MockTransport(lambda r: httpx.Response(403)), **kw)):
            await bg.submit_upload(upload, {})
        self.assertEqual(upload.phase, 'Failed')
        self.assertEqual(upload.backend_id, 'existing')
        self.assertTrue(upload.error)


    async def test_failed_response_is_reconciled_with_accepted_job(self):
        upload=Upload('x',1,phase='Failed',error='Response lost')
        client_class=httpx.AsyncClient
        transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'result':{'uuid':'existing','status':'pending'}}))
        with patch.object(bg.settings,'API_URL','https://test.invalid'), patch.object(bg.httpx,'AsyncClient',lambda **kw:client_class(transport=transport,**kw)):
            await bg.finalize_failure(upload,{'Authorization':'Bearer fixture'})
        self.assertEqual(upload.phase,'Queued')
        self.assertEqual(upload.error,'')
        self.assertEqual(upload.backend_id,'existing')
