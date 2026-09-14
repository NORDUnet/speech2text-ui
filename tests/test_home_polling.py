"""Exercise the page refresh callback without starting NiceGUI or a backend."""
import ast
from pathlib import Path
from textwrap import indent
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from utils.upload_state import Upload, merge_rows

class PollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_refresh_does_not_refetch_file_list(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'pages/home.py').read_text())
        callback = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'update_rows')
        now = [0.0]
        uploads = [Upload('test.mp4', 100)]
        fetch = AsyncMock(return_value=[])
        table = SimpleNamespace(update_rows=Mock())
        button = SimpleNamespace(set_enabled=Mock())
        env = dict(monotonic=lambda: now[0], owner_queue=lambda: uploads,
                   jobs_get=fetch, merge_rows=merge_rows, table=table,
                   delete=button, bulk_export=button, bulk_transcribe=button)
        # Preserve the callback's closure over its page-specific cached listing.
        source = 'def factory():\n    backend_rows = []\n    last_fetch = None\n' + indent(ast.unparse(callback), '    ') + '\n    return update_rows\n'
        exec(source, env)
        update = env['factory']()
        await update(force=False)
        for tick in range(1, 5):
            now[0] = tick
            uploads[0].progress = tick * 10
            await update(force=False)
            self.assertEqual(table.update_rows.call_args.args[0][0]['upload_progress'], f'{tick * 10}% transferred')
        self.assertEqual(fetch.await_count, 1)
        now[0] = 5
        await update(force=False)
        self.assertEqual(fetch.await_count, 2)
        fetch.return_value = [dict(uuid='saved', status='Completed')]
        uploads.clear()
        await update()
        fetch.return_value = None
        now[0] = 40
        await update(force=False)
        self.assertEqual(table.update_rows.call_args.args[0][0]['uuid'], 'saved')
        fetch.return_value = []
        await update()
        self.assertEqual(table.update_rows.call_args.args[0], [])
