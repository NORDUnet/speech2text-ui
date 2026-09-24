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
        table = SimpleNamespace(rows=[])
        table.update_rows = Mock(side_effect=lambda rows, **_: setattr(table, 'rows', rows))
        button = SimpleNamespace(set_enabled=Mock())
        env = dict(monotonic=lambda: now[0], owner_queue=lambda: uploads,
                   jobs_get=fetch, merge_rows=merge_rows, table=table,
                   delete=button, bulk_export=button, bulk_transcribe=button)
        # Preserve the callback's closure over its page-specific cached listing.
        source = 'def factory():\n    backend_rows = []\n    last_fetch = None\n    deleted_ids = set()\n' + indent(ast.unparse(callback), '    ') + '\n    return update_rows\n'
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
        renders = table.update_rows.call_count
        await update(force=False)
        await update()  # Even a fresh identical API listing must not repaint.
        self.assertEqual(table.update_rows.call_count, renders)
        uploads[0].phase = 'Failed'
        uploads[0].error = 'The upload tab was closed before the transfer finished.'
        await update(force=False)
        renders = table.update_rows.call_count
        for tick in range(6, 10):
            now[0] = tick
            await update(force=False)
        self.assertEqual(table.update_rows.call_count, renders)
        self.assertEqual(table.rows[0]['upload_error'], uploads[0].error)
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


    async def test_deleted_rows_stay_gone_during_cached_and_inflight_refresh(self):
        import asyncio
        tree=ast.parse((Path(__file__).resolve().parents[1] / "pages/home.py").read_text())
        callbacks={n.name:n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in {"update_rows","forget_deleted"}}
        old=[dict(uuid="deleted",status="Completed"),dict(uuid="kept",status="Completed")]
        table=SimpleNamespace(rows=list(old),selected=[old[0]])
        table.update_rows=Mock(side_effect=lambda rows,**_:setattr(table,"rows",rows))
        fetch=AsyncMock(return_value=list(old))
        button=SimpleNamespace(set_enabled=Mock())
        env=dict(monotonic=lambda:0,owner_queue=lambda:[],jobs_get=fetch,merge_rows=merge_rows,
                 table=table,delete=button,bulk_export=button,toggle_buttons=Mock())
        source="def factory():\n    backend_rows=[]\n    last_fetch=None\n    deleted_ids=set()\n"
        source+="\n".join(indent(ast.unparse(callbacks[n]),"    ") for n in ("update_rows","forget_deleted"))
        source+="\n    return update_rows,forget_deleted\n"
        exec(source,env)
        update,forget=env["factory"]()
        await update()
        started=asyncio.Event()
        release=asyncio.Event()
        async def stale_fetch(**kwargs):
            started.set()
            await release.wait()
            return list(old)
        fetch.side_effect=stale_fetch
        pending=asyncio.create_task(update())
        await started.wait()
        forget("deleted")
        self.assertEqual([r["uuid"] for r in table.rows],["kept"])
        self.assertEqual(table.selected,[])
        release.set()
        await pending
        await update(force=False)
        self.assertEqual([r["uuid"] for r in table.rows],["kept"])
        fetch.side_effect=None
        fetch.return_value=None
        await update()
        self.assertEqual([r["uuid"] for r in table.rows],["kept"])

    async def test_partial_delete_only_hides_successful_rows(self):
        import httpx
        import sys
        from unittest.mock import patch
        tree=ast.parse((Path(__file__).resolve().parents[1] / "utils/common.py").read_text())
        function=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="__delete_files")
        rows=[dict(uuid="success"),dict(uuid="failed"),dict(uuid="local",local_upload=True)]
        table=SimpleNamespace(rows=rows,selected=list(rows))
        table.update_rows=Mock(side_effect=lambda rows,**_:setattr(table,"rows",rows))
        dialog=SimpleNamespace(close=Mock())
        notify=Mock()
        uploads=[SimpleNamespace(id="local",phase="Failed",backend_id=None)]
        client=AsyncMock()
        client.__aenter__.return_value=client
        client.delete.side_effect=[httpx.Response(200,request=httpx.Request("DELETE","http://test/success")),httpx.RequestError("failed")]
        forgotten=Mock()
        env={"ui":SimpleNamespace(table=object,dialog=object,notify=notify),"httpx":httpx,
             "settings":SimpleNamespace(API_URL="http://test"),"get_auth_header":lambda:{}}
        exec(compile(ast.Module(body=[function],type_ignores=[]),"utils/common.py","exec"),env)
        with patch.object(httpx,"AsyncClient",return_value=client),patch.dict(sys.modules,{"utils.background_upload":SimpleNamespace(owner_queue=lambda:uploads)}):
            await env["__delete_files"](table,dialog,on_deleted=forgotten)
        self.assertEqual([call.args[0] for call in forgotten.call_args_list],["success","local"])
        self.assertEqual([r["uuid"] for r in table.rows],["failed"])
        self.assertEqual(uploads,[])
        self.assertEqual(notify.call_args.kwargs["type"],"warning")
