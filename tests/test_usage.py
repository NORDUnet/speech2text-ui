"""Batched collection must stay off the editing path and omit identity data."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch, AsyncMock


class Client:
    def __enter__(self): return self
    def __exit__(self, *args): pass


class UsageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client=Client()
        self.timers=[]
        self.ui=SimpleNamespace(context=SimpleNamespace(client=self.client),
            timer=lambda interval, callback: self.timers.append((interval,callback)))
        nicegui=ModuleType('nicegui');nicegui.ui=self.ui
        settings=ModuleType('utils.settings');settings.get_settings=lambda:SimpleNamespace(API_URL='https://backend.invalid')
        token=ModuleType('utils.token');token.get_auth_header=lambda:{'Authorization':'Bearer test'}
        spec=importlib.util.spec_from_file_location('isolated_usage',Path(__file__).parents[1]/'utils/usage.py')
        self.module=importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules',{'nicegui':nicegui,'utils.settings':settings,'utils.token':token}):
            spec.loader.exec_module(self.module)

    async def test_many_clicks_one_timer_and_one_anonymous_batch(self):
        for _ in range(100):
            self.module.record('confidence.listen')
        self.module.record('preview.enabled')
        self.assertEqual(len(self.timers),1)
        self.assertEqual(self.timers[0][0],30)
        http=AsyncMock()
        with patch.object(self.module.httpx,'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value=http
            await self.timers[0][1]()
            await self.timers[0][1]()
        http.post.assert_awaited_once()
        self.assertEqual(http.post.call_args.kwargs['json'],
                         {'counters':{'confidence.listen':100,'preview.enabled':1}})

    async def test_failed_flush_is_discarded_without_affecting_actions(self):
        self.module.record('subtitles.checked')
        with patch.object(self.module.httpx,'AsyncClient',side_effect=RuntimeError('offline')):
            await self.timers[0][1]()
        self.assertEqual(dict(self.client._usage_counts),{})
        self.module.record('subtitles.checked')
        self.assertEqual(self.client._usage_counts['subtitles.checked'],1)

    async def test_counter_cap(self):
        self.module.record('confidence.listen',10001)
        self.assertEqual(self.client._usage_counts['confidence.listen'],10000)

if __name__ == '__main__': unittest.main()
