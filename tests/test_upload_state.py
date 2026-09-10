import unittest
from utils.upload_state import Upload, merge_rows

class UploadStateTests(unittest.TestCase):
    def test_upload_not_actionable_until_backend_confirms(self):
        upload = Upload('test.mp4', 10)
        upload.phase = 'Uploaded'
        upload.backend_id = 'real-id'
        pending = [upload]
        self.assertEqual(merge_rows([], pending)[0]['status'], 'Uploading')
        backend = [{'uuid': 'real-id', 'status': 'Uploaded'}]
        self.assertEqual(merge_rows(backend, pending), backend)
        self.assertEqual(pending, [])

    def test_duplicate_filenames_have_distinct_ids(self):
        a, b = Upload('same.mp4', 1), Upload('same.mp4', 1)
        self.assertNotEqual(a.id, b.id)

    def test_early_backend_row_replaces_only_matching_upload(self):
        first, second = Upload('same.mp4', 1), Upload('same.mp4', 1)
        pending = [first, second]
        row = dict(uuid='server-id', upload_id='ui-upload:' + first.id.removeprefix('upload:'), status='Uploading')
        result = merge_rows([row], pending)
        self.assertEqual(len(result), 2)
        self.assertEqual(pending, [second])

    def test_error_remains_visible(self):
        upload = Upload('failed.mp4', 1, phase='Upload failed', error='Interrupted')
        self.assertEqual(merge_rows([], [upload])[0]['upload_error'], 'Interrupted')

    def test_auto_submission_never_exposes_manual_transcribe_early(self):
        upload = Upload('x', 1, backend_id='real', phase='Submitting', options={'language': 'English'})
        pending = [upload]
        backend = [dict(uuid='real', status='Uploaded')]
        row = merge_rows(backend, pending)[0]
        self.assertEqual(row['status'], 'Queuing')
        self.assertTrue(row['local_upload'])
        self.assertEqual(pending, [upload])
        self.assertEqual(backend[0]['status'], 'Uploaded')
        self.assertEqual(merge_rows([dict(uuid='real', status='Queued')], pending)[0]['status'], 'Queued')
        self.assertEqual(pending, [])

    def test_submission_failure_reconciles_lost_success_response(self):
        upload = Upload('x', 1, backend_id='real', phase='Submission failed', error='Retry', options={'language': 'English'})
        pending = [upload]
        row = merge_rows([dict(uuid='real', status='Uploaded')], pending)[0]
        self.assertEqual(row['status'], 'Uploaded')
        self.assertEqual(row['upload_error'], 'Retry')
        self.assertFalse(row.get('local_upload'))
        row = merge_rows([dict(uuid='real', status='Completed')], pending)[0]
        self.assertNotIn('upload_error', row)
        self.assertEqual(pending, [])
