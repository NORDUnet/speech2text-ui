import ast
import unittest
from pathlib import Path
from types import SimpleNamespace, MethodType
from unittest.mock import Mock, patch
from nicegui import ui

ROOT=Path(__file__).parents[1]


class WorkspaceTests(unittest.TestCase):
    def test_search_is_inline_and_controls_keep_working(self):
        tree=ast.parse((ROOT/"utils/srt.py").read_text())
        methods=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in {"create_search_panel","open_search_panel","clear_search"}]
        ns={"ui":ui}
        exec(compile(ast.Module(body=methods,type_ignores=[]),"utils/srt.py","exec"),ns)
        editor=SimpleNamespace(search_term="",search_results=[],current_search_index=0,case_sensitive=False,exact_match=False,
            captions=[SimpleNamespace(index=1,is_highlighted=True)],refresh_display=Mock(),update_search_info=Mock(),
            search_captions=Mock(),navigate_search_results=Mock(),replace_in_current_caption=Mock(),replace_all=Mock())
        for name in ("create_search_panel","open_search_panel","clear_search"):
            setattr(editor,name,MethodType(ns[name],editor))
        with ui.column() as host:
            client=ui.context.client
            with ui.tabs(value="review") as tabs:
                ui.tab("review")
                ui.tab("search")
            editor.tool_tabs=tabs
            with ui.tab_panels(tabs,value="review",animated=False):
                with ui.tab_panel("review"):
                    pass
                with ui.tab_panel("search"):
                    editor.create_search_panel()
            self.assertEqual(editor.search_container.tag,"div")
            field=editor._search_input
            field.set_value("hello")
            handlers={e.type:e.handler for e in field._event_listeners.values()}
            handlers["keydown.enter.exact"]()
            editor.search_captions.assert_called_with("hello")
            editor.search_term="hello"
            editor.search_results=[0,1]
            handlers["keydown.enter.exact"]()
            editor.navigate_search_results.assert_called_with(1)
            handlers["keydown.shift.enter"]()
            editor.navigate_search_results.assert_called_with(-1)
            with patch.object(ui,"timer") as timer,patch.object(field,"run_method") as run:
                editor.open_search_panel()
                self.assertEqual(tabs.value,"search")
                timer.call_args.args[1]()
                self.assertEqual([c.args[0] for c in run.call_args_list],["focus","select"])
            buttons={e.text:e for e in client.elements.values() if isinstance(e,ui.button)}
            replacement=next(e for e in client.elements.values() if isinstance(e,ui.input) and e._props.get("placeholder")=="Replace with…")
            replacement.set_value("world")
            for label,method in [("Replace",editor.replace_in_current_caption),("Replace all",editor.replace_all)]:
                next(e.handler for e in buttons[label]._event_listeners.values() if e.type=="click")()
                method.assert_called_with("world")
            next(e.handler for e in buttons["Clear search"]._event_listeners.values() if e.type=="click")()
            self.assertFalse(editor.captions[0].is_highlighted)
            self.assertEqual(field.value,"")
            # Use the real tool-switch callback to verify cleanup on navigation.
            page=ast.parse((ROOT/"pages/srt.py").read_text())
            switch=next(n for n in ast.walk(page) if isinstance(n,ast.FunctionDef) and n.name=="switch_editor_tool")
            editor._confidence_review_id=None
            editor.search_term="hello"
            editor.search_results=[0]
            editor.captions[0].is_highlighted=True
            env={"editor":editor}
            exec(compile(ast.Module(body=[switch],type_ignores=[]),"pages/srt.py","exec"),env)
            env["switch_editor_tool"](SimpleNamespace(value="speakers"))
            self.assertEqual(editor.search_results,[])
            self.assertFalse(editor.captions[0].is_highlighted)
        host.delete()
