"""Exercise actual editor shortcut dispatch without a browser/backend."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

class ShortcutTests(unittest.TestCase):
    def setUp(self):
        source=ast.parse((Path(__file__).parents[1]/'utils/srt.py').read_text())
        cls=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='SRTEditor')
        cls.body=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('handle_shortcut','save_srt_changes')]
        self.http=SimpleNamespace(put=Mock(return_value=SimpleNamespace(raise_for_status=Mock())),HTTPError=RuntimeError)
        self.ui=SimpleNamespace(run_javascript=Mock(),notify=Mock())
        ns=dict(events=SimpleNamespace(GenericEventArguments=object),ui=self.ui,httpx=self.http,json=json,get_auth_header=lambda:{},settings=SimpleNamespace(API_URL='https://test.invalid'))
        exec(compile(ast.Module(body=[cls],type_ignores=[]),'shortcuts','exec'),ns)
        self.editor=ns['SRTEditor']()
        self.editor.selected_caption=SimpleNamespace(index=3,text='old')
        self.editor.words=[{}];self.editor.data_format='srt';self.editor.filename='file';self.editor.uuid='job'
        self.calls=[]
        for name in ('update_caption_text','split_at_cursor','select_next_caption','select_prev_caption','merge_with_next','merge_with_previous','add_caption_after','remove_caption','undo','redo','open_search_panel','show_export_dialog','mark_as_saved','update_beforeunload_state'):
            setattr(self.editor,name,Mock(side_effect=lambda *args,_name=name:self.calls.append((_name,args))))

    def send(self,action,**data):
        self.editor.handle_shortcut(SimpleNamespace(args=dict(action=action,**data)))

    def test_cursor_split_uses_latest_text_and_correct_caption(self):
        self.send('split',caption_index=3,text='new text',cursor=4)
        self.assertEqual([n for n,args in self.calls],['update_caption_text','split_at_cursor'])
        self.editor.split_at_cursor.assert_called_once_with(self.editor.selected_caption,'new text',4)
        self.send('split',caption_index=2,text='wrong caption',cursor=4)
        self.assertEqual(self.editor.split_at_cursor.call_count,1)

    def test_all_dispatches_and_missing_selection(self):
        pairs={'next':'select_next_caption','previous':'select_prev_caption','merge_next':'merge_with_next','merge_previous':'merge_with_previous','delete':'remove_caption','undo':'undo','redo':'redo','find':'open_search_panel','export':'show_export_dialog'}
        for action,method in pairs.items():
            self.send(action)
            getattr(self.editor,method).assert_called_once()
        self.send('close');self.ui.run_javascript.assert_called_once()
        self.editor.selected_caption=None
        for action in ('add','split','delete','merge_next','merge_previous'):
            self.send(action)
        self.editor.add_caption_after.assert_not_called()
        self.editor.remove_caption.assert_called_once()

    def test_transcript_only_add(self):
        self.send('add');self.editor.add_caption_after.assert_not_called()
        self.editor.data_format='txt';self.send('add');self.editor.add_caption_after.assert_called_once()

    def test_save_uses_supported_formats_and_failure_does_not_mark_saved(self):
        self.editor.export_srt=lambda:'subtitle'
        self.editor.export_json=lambda:{'segments':[]}
        for format,expected in [('srt','srt'),('txt','json')]:
            self.editor.srt_format=format
            self.send('save')
            self.assertEqual(self.http.put.call_args.kwargs['json']['format'],expected)
        self.editor.mark_as_saved.reset_mock()
        self.http.put.side_effect=RuntimeError('offline')
        self.send('save');self.editor.mark_as_saved.assert_not_called()

if __name__=='__main__':unittest.main()
