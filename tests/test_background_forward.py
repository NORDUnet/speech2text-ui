import asyncio
import gc
import json
import weakref
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
from nicegui import core, ui
from nicegui.elements.upload_files import LargeFileUpload, SmallFileUpload, create_file_upload
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser
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
            await bg.forward_file(upload, LargeFileUpload('test.mp4', 'video/mp4', Path(path)), {'Authorization': 'Bearer fixture'}, storage)
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

    async def test_retained_upload_survives_callback_and_uploader_deletion(self):
        core.loop = asyncio.get_running_loop()
        data = b'example' * 1000
        original = tempfile.SpooledTemporaryFile(max_size=1024)
        original.write(data)
        original.seek(0)
        with patch.object(MultiPartParser, 'spool_max_size', 1024):
            source = await create_file_upload(UploadFile(original, filename='test.mp4'))
        self.assertTrue(original.closed)
        self.assertIsInstance(source, LargeFileUpload)
        path, reference = source._path, weakref.ref(source)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        upload = Upload('test.mp4', len(data), received=True)
        received, started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        task = None
        async def receive(event):
            nonlocal task
            task = asyncio.create_task(bg.forward_file(upload, event.file, {}))
            bg._tasks.add(task)
            task.add_done_callback(bg._tasks.discard)
            received.set()
        client_class = httpx.AsyncClient
        async def handle(request):
            self.assertEqual(await request.aread(), data)
            return httpx.Response(200, json={'result': {'uuid': 'backend-id'}})
        class PausedClient(client_class):
            async def post(self, *args, **kwargs):
                started.set()
                await release.wait()
                return await super().post(*args, **kwargs)
        with patch.object(bg.settings, 'API_URL', 'https://test.invalid'), patch.object(bg.httpx, 'AsyncClient', lambda **kw: PausedClient(transport=httpx.MockTransport(handle), **kw)):
            uploader = ui.upload(on_upload=receive)
            await uploader.handle_uploads([source])
            await received.wait()
            await started.wait()
            del source
            uploader.delete()
            del uploader
            for _ in range(3):
                await asyncio.sleep(0)
            gc.collect()
            self.assertIsNotNone(reference())
            self.assertTrue(path.exists())
            release.set()
            await task
            await asyncio.sleep(0)
            self.assertNotIn(task, bg._tasks)
        self.assertEqual(upload.phase, 'Uploaded')
        self.assertIsNone(reference())
        self.assertFalse(path.exists())

    async def test_interrupted_stream_closes_iterator_and_releases_file(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                started = asyncio.Event()
                closed = asyncio.Event()
                with tempfile.NamedTemporaryFile(delete=False) as stream:
                    stream.write(b'example')
                    path = Path(stream.name)
                self.addCleanup(lambda p=path: p.unlink(missing_ok=True))
                class ObservedUpload(LargeFileUpload):
                    async def iterate(self, *, chunk_size=1024 * 1024):
                        try:
                            async for chunk in super().iterate(chunk_size=chunk_size):
                                yield chunk
                        finally:
                            closed.set()
                source = ObservedUpload('test.mp4', 'video/mp4', path)
                reference = weakref.ref(source)
                upload = Upload('test.mp4', 7, received=True)
                class InterruptedClient:
                    async def __aenter__(self):
                        return self
                    async def __aexit__(self, *args):
                        pass
                    async def post(self, url, **kwargs):
                        self_chunk = await anext(kwargs['content'])
                        assert self_chunk == b'example'
                        started.set()
                        if cancelled:
                            await asyncio.Event().wait()
                        raise httpx.WriteError('connection lost')
                with patch.object(bg.httpx, 'AsyncClient', lambda **kw: InterruptedClient()), patch.object(bg, 'finalize_failure', new_callable=AsyncMock) as fail, patch.object(bg, 'submit_upload', new_callable=AsyncMock) as submit:
                    task = asyncio.create_task(bg.forward_file(upload, source, {}))
                    del source
                    await started.wait()
                    if cancelled:
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    self.assertEqual(task.cancelled(), cancelled)
                    self.assertTrue(closed.is_set())
                    self.assertEqual(upload.phase, 'Failed')
                    submit.assert_not_awaited()
                    self.assertEqual(fail.await_count, 0 if cancelled else 1)
                    del task
                for _ in range(10):
                    await asyncio.sleep(0.01)
                    gc.collect()
                    if reference() is None:
                        break
                self.assertIsNone(reference())
                self.assertFalse(path.exists())

    async def test_small_upload_streams_without_save(self):
        source = SmallFileUpload('test.mp4', 'video/mp4', b'example')
        upload = Upload('test.mp4', 7, received=True)
        client_class = httpx.AsyncClient
        async def handle(request):
            self.assertEqual(await request.aread(), b'example')
            return httpx.Response(200, json={'result': {'uuid': 'backend-id'}})
        with patch.object(source, 'save', new_callable=AsyncMock) as save, patch.object(bg.settings, 'API_URL', 'https://test.invalid'), patch.object(bg.httpx, 'AsyncClient', lambda **kw: client_class(transport=httpx.MockTransport(handle), **kw)):
            await bg.forward_file(upload, source, {})
        save.assert_not_awaited()
        self.assertEqual(upload.phase, 'Uploaded')

    async def test_oversized_upload_not_forwarded(self):
        source = SmallFileUpload('test.mp4', 'video/mp4', b'example')
        upload = Upload('test.mp4', 7, received=True)
        with patch.object(bg.settings, 'MAX_UPLOAD_BYTES', 6), patch.object(bg.httpx, 'AsyncClient') as client, patch.object(bg, 'finalize_failure', new_callable=AsyncMock):
            await bg.forward_file(upload, source, {})
        client.assert_not_called()
        self.assertEqual(upload.phase, 'Failed')
