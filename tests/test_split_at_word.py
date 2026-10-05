import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from utils.caption import SRTCaption

class SplitAtWordTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(Path(__file__).parents[1].joinpath("utils/srt.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ("split_at_word", "reflow_split_text", "split_at_cursor")]
        ns = {"CHARACTER_LIMIT":42,"re":re,"SRTCaption":SRTCaption,"ui":SimpleNamespace(notify=lambda *a,**k:None)}
        exec(compile(ast.Module(body=[cls],type_ignores=[]),"split","exec"),ns)
        self.editor = ns[cls.name]()
        self.caption = SRTCaption(1,"00:00:00,000","00:00:05,000","one  two\nthree", "speaker", "original")
        self.editor.captions = [self.caption]
        self.saved = []
        self.editor.save_state_for_undo = lambda: self.saved.append(self.caption.copy())
        self.editor.caption_word_times = lambda c: [("one",0,1,None,{}),("two",1.123,2,None,{}),("three",3,4,None,{})]
        self.editor.renumber_captions = lambda: None
        self.editor.update_words_per_minute = lambda: None
        self.editor.refresh_display = lambda **kw: None

    def test_split_preserves_text_speaker_and_snapshot(self):
        self.assertTrue(self.editor.split_at_word(self.caption,1,self.caption.text))
        first, second = self.editor.captions
        self.assertEqual((first.text,second.text),("one","two three"))
        self.assertEqual(first.end_time,"00:00:01,123")
        self.assertEqual(first.end_time,second.start_time)
        self.assertEqual(second.end_time,"00:00:05,000")
        self.assertEqual(second.generated_speaker,"original")
        self.assertEqual(self.saved[0].text,"one  two\nthree")

    def test_reflow_removes_orphan_line(self):
        self.assertEqual(self.editor.reflow_split_text("two\nthree four"), "two three four")
        text = self.editor.reflow_split_text("one\ntwo three four five six seven eight nine ten eleven twelve")
        self.assertTrue(all(len(line.split()) >= 2 for line in text.splitlines()))
        self.assertEqual(text.split(), "one two three four five six seven eight nine ten eleven twelve".split())

    def test_cursor_in_whitespace(self):
        self.assertTrue(self.editor.split_at_cursor(self.caption, self.caption.text, 4))
        self.assertEqual(self.editor.captions[1].text, "two three")

    def test_cursor_inside_word_keeps_word_whole(self):
        self.assertTrue(self.editor.split_at_cursor(self.caption, self.caption.text, 6))
        self.assertEqual(self.editor.captions[1].text, "two three")

    def test_cursor_at_end_rejected(self):
        self.assertFalse(self.editor.split_at_cursor(self.caption, self.caption.text, len(self.caption.text)))
        self.assertFalse(self.saved)

    def test_stale_selection_rejected(self):
        self.assertFalse(self.editor.split_at_word(self.caption,1,"old text"))
        self.assertFalse(self.saved)

    def test_unaligned_word_rejected(self):
        self.editor.caption_word_times = lambda c: [("one",0,1,None,{}),("two",None,None,None,{})]
        self.assertFalse(self.editor.split_at_word(self.caption,1,self.caption.text))
        self.assertFalse(self.saved)

if __name__ == "__main__": unittest.main()
