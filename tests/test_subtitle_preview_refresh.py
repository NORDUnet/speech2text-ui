"""Preview refresh must survive replacement of the edited card's slot."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


class PreviewRefreshTests(unittest.TestCase):
    def test_card_rebuild_does_not_cancel_refresh_or_block_later_edits(self):
        source = Path(__file__).resolve().parents[1] / 'utils/srt.py'
        tree = ast.parse(source.read_text())
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'refresh_subtitle_preview')
        slot = ['card']
        timers, sent = [], []
        class Client:
            def __enter__(self):
                self.previous = slot[0]
                slot[0] = 'page'
            def __exit__(self, *_):
                slot[0] = self.previous
        def timer(delay, callback, once):
            timers.append((slot[0], callback))
        ui = SimpleNamespace(context=SimpleNamespace(client=Client()), timer=timer, run_javascript=sent.append)
        env = dict(ui=ui, json=json)
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), env)
        refresh = env['refresh_subtitle_preview']
        caption = SimpleNamespace(text='original', get_start_seconds=lambda: 1, get_end_seconds=lambda: 3)
        editor = SimpleNamespace(subtitle_preview=True, captions=[caption], _preview_refresh_pending=False)
        refresh(editor)
        caption.text = 'edited'
        refresh(editor)
        self.assertEqual(len(timers), 1, 'Coalesce pending edits')
        # Deleting the card also deletes any timer parented by that card.
        surviving = [callback for owner, callback in timers if owner != 'card']
        self.assertEqual(len(surviving), 1)
        surviving[0]()
        self.assertIn('edited', sent[-1])
        self.assertFalse(editor._preview_refresh_pending)
        timers.clear()
        caption.text = 'second edit'
        refresh(editor)
        timers[0][1]()
        self.assertIn('second edit', sent[-1])
        editor.subtitle_preview = False
        refresh(editor, force=True)
        self.assertIn('[], false', sent[-1])
