"""Session-scoped upload progress, independent of any page's elements."""
from dataclasses import dataclass, field
from uuid import uuid4
from time import perf_counter

@dataclass
class Upload:
    filename: str
    size: int
    id: str = field(default_factory=lambda: 'upload:' + uuid4().hex)
    phase: str = 'Uploading'
    received: bool = False
    progress: int = 0
    started_at: float = field(default_factory=perf_counter)
    backend_id: str | None = None
    error: str = ''
    options: dict = field(default_factory=dict)

    def progress_label(self):
        if self.phase != 'Uploading':
            return ''
        return 'Saving file…' if self.received else f'{self.progress}% transferred'

    def row(self):
        return dict(uuid=self.id, id=self.id, filename=self.filename,
                    status="Uploading" if self.phase == "Uploaded" else self.phase, upload_error=self.error, local_upload=True, upload_progress=self.progress_label(),
                    created_at='', updated_at='', deletion_date='', job_type='',
                    language='', model_type='', output_format='')

# Each owner is an unguessable key held in NiceGUI's server-side user storage.
# No tokens, file contents or encryption passphrases are stored in this registry.
queues: dict[str, list[Upload]] = {}


def merge_rows(rows, uploads):
    # Copy backend rows: rendering must not mutate the API snapshot.
    rows = [dict(row) for row in rows]
    by_id = {row['uuid']: row for row in rows}
    by_upload = {row.get('upload_id'): row for row in rows if row.get('upload_id')}
    retained, placeholders = [], []
    for upload in uploads:
        row = by_upload.get('ui-upload:' + upload.id.removeprefix('upload:'))
        if row is None:
            row = by_id.get(upload.backend_id)
        if row is None:
            retained.append(upload)
            placeholders.append(upload.row())
            continue
        upload.backend_id = row['uuid']
        if not upload.options:
            continue
        if row['status'] in ('Queued', 'Transcribing', 'Completed', 'Failed'):
            continue
        retained.append(upload)
        if upload.phase == 'Submission failed' and row['status'] == 'Uploaded':
            # File is safely stored. Normal Transcribe is the recovery action.
            row['upload_error'] = upload.error
        elif upload.phase == 'Upload failed':
            row.update(status='Upload failed', upload_error=upload.error)
        else:
            row.update(status='Uploading' if row['status'] == 'Uploading' else 'Queuing',
                       local_upload=True, upload_progress=upload.progress_label())
    uploads[:] = retained
    return placeholders + rows
