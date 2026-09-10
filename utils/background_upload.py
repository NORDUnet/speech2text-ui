"""Persistent browser upload host; content pages navigate in a same-origin frame."""
import asyncio
import json
import os
import secrets
import tempfile
from urllib.parse import urlsplit

import aiofiles
import httpx
from nicegui import app, ui
from utils.settings import get_settings
from utils.token import get_auth_header, get_user_status, token_refresh
from utils.upload_state import Upload, queues
from utils.transcription_options import transcription_options

settings = get_settings()
_tasks = set()


def owner_queue():
    storage = app.storage.user
    if '_upload_owner' not in storage:
        storage['_upload_owner'] = secrets.token_hex(24)
    return queues.setdefault(storage['_upload_owner'], [])


async def submit_upload(upload, headers, user_storage=None):
    """Retry transient failures with the upload identity, never as a new job."""
    upload.phase = 'Submitting'
    options = upload.options
    payload = dict(language=options['language'] + (' (verbatim)' if options['verbatim'] else ''),
                   speakers=options['speakers'],
                   output_format='SRT' if options['output_format'] == 'Subtitles' else 'TXT',
                   encryption_password='')
    for attempt in range(2):
        current_headers = dict(headers)
        if user_storage is not None and user_storage.get('token'):
            current_headers['Authorization'] = 'Bearer ' + user_storage['token']
        current_headers['X-Upload-Id'] = upload.id.removeprefix('upload:')
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.put(settings.API_URL + '/api/v1/transcriber/' + upload.backend_id,
                                            headers=current_headers, json=payload)
                if response.status_code >= 500 and attempt == 0:
                    await asyncio.sleep(1)
                    continue
                response.raise_for_status()
                if response.json()['result']['status'] not in ('pending', 'in_progress', 'completed'):
                    raise ValueError('Job was not queued')
                upload.phase = 'Queued'
                upload.error = ''
                return
        except httpx.HTTPStatusError as error:
            detail = 'Automatic transcription could not be queued. Use Transcribe to try again.'
            if error.response.status_code == 403:
                detail = 'Transcription was refused. Check your quota or sign in again, then use Transcribe.'
            upload.error = detail
            break
        except (httpx.RequestError, ValueError, KeyError):
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            upload.error = 'Could not confirm transcription submission. Refresh the table before trying Transcribe again.'
    upload.phase = 'Submission failed'


async def forward_file(upload, path, headers, user_storage=None):
    async def chunks():
        async with aiofiles.open(path, 'rb') as stream:
            while chunk := await stream.read(1024 * 1024):
                yield chunk
    try:
        async with httpx.AsyncClient(timeout=900) as client:
            result = await client.post(settings.API_URL + '/api/v1/transcriber/stream',
                                       params={'filename': upload.filename}, headers={**headers, 'X-Upload-Id': upload.id.removeprefix('upload:')},
                                       content=chunks())
            result.raise_for_status()
            upload.backend_id = result.json()['result']['uuid']
            upload.phase = 'Uploaded'
    except Exception:
        upload.phase = 'Upload failed'
        upload.error = 'Could not store this file. Please upload it again.'
    finally:
        os.unlink(path)
    if upload.phase != 'Uploaded':
        return
    if user_storage is not None:
        drafts = dict(user_storage.get('upload_job_settings', {}))
        drafts[upload.backend_id] = dict(upload.options)
        user_storage['upload_job_settings'] = drafts
    if upload.options:
        await submit_upload(upload, headers, user_storage)


def register():
    @ui.page('/workspace')
    def workspace(view: str = '/home'):
        if not token_refresh() or not get_user_status():
            ui.navigate.to('/')
            return
        from utils.common import default_styles, sanitize_filename, _default_transcription_language
        ui.add_head_html(default_styles)
        ui.add_head_html('<style>.nicegui-content{padding:0!important} .upload-overlay{position:fixed;inset:0;z-index:5000;background:#0005;align-items:center;justify-content:center}</style>')
        parsed = urlsplit(view)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith('/') or parsed.path in ('/', '/workspace', '/logout'):
            view = '/home'
        client = ui.context.client
        uploads = owner_queue()
        batch = []
        user_storage = app.storage.user
        starting = False
        frame = ui.element('iframe').props('title="Speech2Text workspace"').style('width:100%;height:100vh;border:0;display:block')
        frame._props['src'] = view
        frame.props('id=workspace-content')
        with ui.column().classes('upload-overlay') as overlay:
            with ui.card().style('width:560px;max-width:94vw;max-height:90vh;overflow-y:auto;padding:24px;gap:16px'):
                ui.label('Upload & transcription settings').classes('text-lg font-medium')
                ui.label('Choose up to 5 files. Each will be transcribed automatically after uploading.').classes('text-sm')

                async def receive(event):
                    item = next((u for u in batch if not u.received and u.filename == sanitize_filename(event.file.name)), None)
                    if item is None:
                        return
                    headers = dict(get_auth_header() or {})
                    item.received = True
                    item.phase = 'Uploading'
                    fd, path = tempfile.mkstemp(prefix='scribe-ui-upload-', suffix='.upload', dir=settings.UPLOAD_TMP_DIR or None)
                    os.close(fd)
                    try:
                        await event.file.save(path)
                        if os.path.getsize(path) > settings.MAX_UPLOAD_BYTES:
                            raise ValueError('File too large')
                        # Snapshot credentials while the authenticated host still exists.
                        task = asyncio.create_task(forward_file(item, path, headers, user_storage))
                        _tasks.add(task)
                        task.add_done_callback(_tasks.discard)
                    except Exception:
                        os.unlink(path)
                        item.phase = 'Upload failed'
                        item.error = 'Could not receive this file. Check its size and try again.'

                uploader = ui.upload(label='Choose files', on_upload=receive, multiple=True, auto_upload=False,
                                     max_files=5, max_file_size=settings.MAX_UPLOAD_BYTES,
                                     on_rejected=lambda: ui.notify("Choose up to 5 supported media files within the upload size limit.", type="warning")).classes('w-full')
                uploader.props('flat bordered color=grey-2 text-color=grey-9 hide-upload-btn accept=.mp3,.wav,.flac,.mp4,.mkv,.avi,.m4a,.aiff,.aif,.mov,.ogg,.opus,.webm,.wma,.mpg,.mpeg')

                uploader.add_slot('header', r"""
                    <div class="row items-center full-width q-pa-sm">
                        <div class="col">
                            <div class="text-subtitle2">Choose files</div>
                            <div class="text-caption">{{ props.uploadSizeLabel }}</div>
                        </div>
                        <q-btn v-if="props.canAddFiles" flat round dense icon="add" aria-label="Choose files" @click="props.pickFiles">
                            <q-uploader-add-trigger />
                            <q-tooltip>Add files</q-tooltip>
                        </q-btn>
                    </div>
                """)
                uploader.add_slot('list', r"""
                    <q-list separator>
                        <q-item v-for="file in props.files" :key="file.__key">
                            <q-item-section style="min-width:0">
                                <q-item-label style="overflow-wrap:anywhere">{{ file.name }}</q-item-label>
                                <q-item-label caption>{{ file.__sizeLabel }}</q-item-label>
                            </q-item-section>
                            <q-item-section side>
                                <q-btn flat round dense icon="close" :aria-label="'Remove ' + file.name" @click="props.removeFile(file)">
                                    <q-tooltip>Remove file</q-tooltip>
                                </q-btn>
                            </q-item-section>
                        </q-item>
                    </q-list>
                """)

                ui.separator()
                language = ui.select(settings.WHISPER_LANGUAGES, label='Language',
                                     value=_default_transcription_language()).classes('w-full').props('outlined dense')
                output_format = ui.radio(['Transcript', 'Subtitles'], value='Transcript').props('inline')
                with ui.expansion('More options', icon='tune').classes('w-full'):
                    speakers = ui.number('Number of speakers', value=0, min=0, step=1).classes('w-full').props('outlined dense')
                    ui.label('0 detects the number automatically.').classes('text-xs opacity-60')
                    verbatim = ui.checkbox('Verbatim — include filler words and repetitions', value=False)
                def update_verbatim():
                    supported = language.value.lower() in ('swedish', 'norwegian')
                    verbatim.set_visibility(supported)
                    if not supported:
                        verbatim.value = False
                language.on_value_change(lambda _: update_verbatim())
                update_verbatim()
                remember = ui.checkbox('Remember output type and advanced options', value=False).classes('text-sm')
                remember.tooltip('Remembers output type, speaker count and verbatim. Default language is managed in User settings.')

                async def begin_upload():
                    nonlocal starting
                    if starting:
                        return
                    starting = True
                    start_button.disable()
                    try:
                        options = transcription_options(language.value, output_format.value,
                                                        speakers.value, verbatim.value, settings.WHISPER_LANGUAGES)
                        files = await ui.run_javascript(f"getElement({uploader.id}).$refs.qRef.files.map(f => ({{name:f.name,size:f.size}}))")
                        if not files:
                            ui.notify('Choose at least one file.', type='warning')
                            return
                        if len(files) > 5 or any(not 0 <= int(f['size']) <= settings.MAX_UPLOAD_BYTES for f in files):
                            ui.notify('Choose up to 5 files within the upload size limit.', type='warning')
                            return
                        batch.clear()
                        for metadata in files:
                            item = Upload(sanitize_filename(metadata['name']), int(metadata['size']), options=dict(options))
                            batch.append(item)
                            uploads.append(item)
                        if remember.value:
                            user_storage['upload_defaults'] = {key: options[key] for key in ('output_format', 'speakers', 'verbatim')}
                        overlay.set_visibility(False)
                        await ui.run_javascript(f'window.scribeUploading = true; getElement({uploader.id}).$refs.qRef.upload();')
                    except ValueError as error:
                        ui.notify(str(error), type='warning')
                    finally:
                        starting = False
                        start_button.enable()

                def failed():
                    for item in batch:
                        if not item.received:
                            item.phase = 'Upload failed'
                            item.error = 'Transfer interrupted. Please upload this file again.'
                uploader.on('failed', failed)
                with ui.row().classes('w-full justify-end gap-2'):
                    ui.button('Cancel', on_click=lambda: overlay.set_visibility(False)).props('flat no-caps', remove='color')
                    start_button = ui.button('Upload & transcribe', on_click=begin_upload).props('unelevated no-caps')
        overlay.set_visibility(False)

        def open_upload():
            if any(not u.received and u.phase == 'Uploading' for u in batch):
                ui.notify('An upload is already transferring. You can add more files when it finishes.')
                return
            uploader.reset()
            defaults = user_storage.get('upload_defaults', {})
            # Ignore language saved by earlier upload dialogs; User settings owns it.
            language.value = _default_transcription_language()
            output_format.value = defaults.get('output_format', 'Transcript')
            speakers.value = defaults.get('speakers', 0)
            verbatim.value = defaults.get('verbatim', False)
            remember.value = False
            update_verbatim()
            overlay.set_visibility(True)
        ui.button(on_click=open_upload).props('id=background-upload-launcher').style('display:none')
        ui.add_body_html('''<script>
        window.scribeUploading = false;
        window.addEventListener('beforeunload', e => {
            if (window.scribeUploading) { e.preventDefault(); e.returnValue = ''; }
        });
        window.addEventListener('message', e => {
            const frame = document.getElementById('workspace-content');
            if (e.origin !== location.origin || e.source !== frame?.contentWindow) return;
            if (e.data === 'scribe:upload') document.getElementById('background-upload-launcher')?.click();
        });
        </script>''')
        async def track_navigation():
            await client.connected()
            await ui.run_javascript("""
                const frame = document.getElementById('workspace-content');
                frame.addEventListener('load', () => {
                    try {
                        const loc = frame.contentWindow.location;
                        if (loc.origin !== location.origin) return;
                        if (loc.pathname === '/' || loc.pathname === '/logout') {
                            window.location.href = loc.href;
                            return;
                        }
                        history.replaceState(null, '', '/workspace?view=' + encodeURIComponent(loc.pathname + loc.search));
                    } catch (_) { /* External pages are outside the application's frame. */ }
                });
            """)
        ui.timer(0.1, track_navigation, once=True)
        async def sync():
            transferring = any(not u.received and u.phase == 'Uploading' for u in batch)
            if transferring:
                try:
                    progress = await ui.run_javascript(f"getElement({uploader.id}).$refs.qRef.files.map(f => f.__progress || 0)", timeout=2)
                    for item, fraction in zip(batch, progress):
                        if not item.received:
                            item.progress = max(item.progress, min(100, max(0, int(float(fraction) * 100))))
                except (TimeoutError, RuntimeError):
                    pass  # A brief client disconnect must not fail the upload.
            ui.run_javascript('window.scribeUploading = ' + json.dumps(transferring) + ';')
        ui.timer(0.5, sync)
        ui.timer(30, token_refresh)
        def disconnected():
            for item in batch:
                if not item.received and item.phase == 'Uploading':
                    item.phase = 'Upload failed'
                    item.error = 'The upload tab was closed before the transfer finished.'
        client.on_delete(disconnected)
