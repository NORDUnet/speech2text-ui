"""Upload quota display: warning threshold, refreshed values, and errors."""
import unittest
from unittest.mock import Mock, patch

import httpx

from utils import background_upload as bg


class QuotaDisplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_remaining_hours_and_strict_five_hour_warning(self):
        client_class = httpx.AsyncClient
        for remaining, expected, red in (
            (17999, 'Remaining quota: 4h 59m', True),
            (18000, 'Remaining quota: 5h 00m', False),
            (9000, 'Remaining quota: 2h 30m', True),
            (0, 'Remaining quota: 0h 00m', True),
            (None, '', False),
        ):
            with self.subTest(remaining=remaining):
                label = Mock(is_deleted=False)
                async def handle(request):
                    self.assertEqual(request.headers['Authorization'], 'Bearer fixture')
                    return httpx.Response(200, json={'result': {'remaining_seconds': remaining}})

                with patch.object(bg.httpx, 'AsyncClient', lambda **kw: client_class(transport=httpx.MockTransport(handle), **kw)):
                    await bg.refresh_upload_quota(label, {'Authorization': 'Bearer fixture'})
                self.assertEqual(label.set_text.call_args.args[0], expected)
                self.assertEqual(label.set_visibility.call_args.args[0], remaining is not None)
                label.classes.assert_any_call(remove='text-negative')
                additions = [call for call in label.classes.call_args_list if call.kwargs.get('add') == 'text-negative']
                self.assertEqual(bool(additions), red)

    async def test_service_error_does_not_show_stale_quota(self):
        label = Mock(is_deleted=False)
        client_class = httpx.AsyncClient
        transport = httpx.MockTransport(lambda request: httpx.Response(503))
        with patch.object(bg.httpx, 'AsyncClient', lambda **kw: client_class(transport=transport, **kw)):
            await bg.refresh_upload_quota(label, {})
        self.assertEqual(label.set_text.call_args.args[0], 'Remaining quota: Unavailable')
        label.classes.assert_called_once_with(remove='text-negative')
