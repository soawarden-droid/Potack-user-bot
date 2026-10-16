"""Android bridge lifecycle tests: fake Telegram client, no real account access."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telethon import errors


class AndroidBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'android/app/src/main/python/android_bridge.py'
        spec = importlib.util.spec_from_file_location('android_bridge_test', source)
        self.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bridge)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.client = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(),
            is_connected=lambda: True,
            is_user_authorized=AsyncMock(return_value=False),
            send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash='private-hash')),
            sign_in=AsyncMock(), set_receive_updates=AsyncMock(), log_out=AsyncMock(return_value=True),
            get_me=AsyncMock(return_value=SimpleNamespace(phone='1234567890')))
        self.bot = SimpleNamespace(client=self.client, research=SimpleNamespace(start=AsyncMock(), stop=AsyncMock()))
        self.values = {'api_id': '12345', 'api_hash': 'a'*32, 'phone': '+1234567890'}

    async def login(self):
        with patch.object(self.bridge.importlib, 'import_module', return_value=self.bot), patch.dict(os.environ):
            await self.bridge._dispatch(self.home, 'login', self.values)

    def state(self):
        return json.loads(self.bridge.snapshot())

    async def test_login_code_start_stop_and_private_paths(self):
        await self.login()
        self.assertEqual(self.state()['phase'], 'code')
        self.client.set_receive_updates.assert_not_awaited()
        self.bot.research.start.assert_not_awaited()
        config_path = self.home/'potack_private.json'
        config = json.loads(config_path.read_text())
        self.assertTrue(config['android_paused'])
        self.assertEqual(config['session_name'], 'android')
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('private-hash', self.bridge.snapshot())
        await self.bridge._dispatch(self.home, 'code', {'code': '12345'})
        self.assertEqual(self.state()['phase'], 'ready')
        self.client.is_user_authorized.return_value = True
        await self.bridge._dispatch(self.home, 'start', {})
        self.assertTrue(self.state()['running'])
        self.client.set_receive_updates.assert_awaited_with(True)
        await self.bridge._dispatch(self.home, 'stop', {})
        self.assertFalse(self.state()['running'])
        self.client.set_receive_updates.assert_awaited_with(False)
        self.bot.research.stop.assert_awaited()
        self.client.disconnect.assert_awaited()

    async def test_two_factor_flow_and_redacted_errors(self):
        await self.login()
        self.client.sign_in.side_effect = errors.SessionPasswordNeededError(None)
        await self.bridge._dispatch(self.home, 'code', {'code': '12345'})
        self.assertEqual(self.state()['phase'], 'password')
        self.client.sign_in.side_effect = errors.PasswordHashInvalidError(None)
        await self.bridge._dispatch(self.home, 'password', {'password': 'SECRET_PASSWORD'})
        self.assertEqual(self.state()['phase'], 'password')
        self.client.sign_in.side_effect = RuntimeError('SECRET_PASSWORD')
        await self.bridge._dispatch(self.home, 'password', {'password': 'SECRET_PASSWORD'})
        self.assertNotIn('SECRET_PASSWORD', self.bridge.snapshot())

    async def test_validation_and_unauthorized_start(self):
        await self.bridge._dispatch(self.home, 'login', {**self.values, 'api_hash': 'invalid'})
        self.assertEqual(self.state()['phase'], 'error')
        self.assertFalse((self.home/'potack_private.json').exists())
        await self.login()
        await self.bridge._dispatch(self.home, 'start', {})
        self.assertFalse(self.state()['running'])
        self.bot.research.start.assert_not_awaited()

    async def test_settings_cannot_replace_a_running_client(self):
        await self.login()
        self.client.is_user_authorized.return_value = True
        await self.bridge._dispatch(self.home, 'start', {})
        await self.bridge._dispatch(self.home, 'login', self.values)
        self.assertTrue(self.state()['running'])
        self.assertIn('Stop the userbot', self.state()['message'])

    async def test_logout_removes_phone_credentials_and_resets_client(self):
        await self.login()
        # Do not alter another test's loaded desktop module.
        with patch.dict(self.bridge.sys.modules):
            await self.bridge._dispatch(self.home, 'logout', {})
        self.client.log_out.assert_awaited_once()
        self.assertIsNone(self.bridge._bot)
        self.assertFalse((self.home/'potack_private.json').exists())
        self.assertFalse(self.state()['running'])

    async def test_saved_session_cannot_silently_login_as_a_different_phone(self):
        self.client.is_user_authorized.return_value = True
        self.client.get_me.return_value = SimpleNamespace(phone='9999999999')
        await self.login()
        self.assertEqual(self.state()['phase'], 'error')
        self.assertIn('another saved account', self.state()['message'])

    async def test_service_shutdown_marks_listener_offline_and_preserves_errors(self):
        await self.login()
        self.client.is_user_authorized.return_value = True
        await self.bridge._dispatch(self.home, 'start', {})
        await self.bridge._dispatch(self.home, 'shutdown', {})
        self.assertEqual(self.state()['phase'], 'offline')
        self.assertFalse(self.state()['running'])
        self.bridge._set('error', 'Network failed.')
        await self.bridge._dispatch(self.home, 'shutdown', {})
        self.assertEqual(self.state()['message'], 'Network failed.')

    async def test_java_dispatch_owns_a_background_event_loop(self):
        try:
            with patch.object(self.bridge.importlib, 'import_module', return_value=self.bot), patch.dict(os.environ):
                raw = await asyncio.to_thread(self.bridge.dispatch, str(self.home), 'login', json.dumps(self.values))
            self.assertEqual(json.loads(raw)['phase'], 'code')
            self.assertFalse(json.loads(raw)['busy'])
            self.assertIsNot(self.bridge._loop, asyncio.get_running_loop())
            raw = await asyncio.to_thread(self.bridge.dispatch, str(self.home), 'stop')
            self.assertEqual(json.loads(raw)['phase'], 'offline')
        finally:
            if self.bridge._loop:
                self.bridge._loop.call_soon_threadsafe(self.bridge._loop.stop)
                await asyncio.to_thread(self.bridge._thread.join, 2)
                self.bridge._loop.close()

    async def test_status_reports_lost_connection_and_recovery(self):
        await self.login()
        self.client.is_user_authorized.return_value = True
        await self.bridge._dispatch(self.home, 'start', {})
        self.client.is_connected = lambda: False
        self.assertEqual(self.state()['phase'], 'disconnected')
        self.client.is_connected = lambda: True
        self.assertEqual(self.state()['phase'], 'online')

    async def test_empty_code_does_not_trigger_another_code_request_or_mark_ready(self):
        await self.login()
        await self.bridge._dispatch(self.home, 'code', {'code': ''})
        self.assertEqual(self.state()['phase'], 'code')
        self.client.sign_in.assert_not_awaited()
        self.client.send_code_request.assert_awaited_once()
        await self.bridge._dispatch(self.home, 'password', {'password': ''})
        self.assertEqual(self.state()['phase'], 'password')
        self.client.sign_in.assert_not_awaited()

    async def test_stop_interrupts_a_pending_network_login(self):
        entered, cancelled = threading.Event(), threading.Event()
        async def slow_connect():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.client.connect.side_effect = slow_connect
        task = None
        try:
            with patch.object(self.bridge.importlib, 'import_module', return_value=self.bot), patch.dict(os.environ):
                task = asyncio.create_task(asyncio.to_thread(self.bridge.dispatch, str(self.home), 'login', json.dumps(self.values)))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.bridge.request_stop()
                raw = await asyncio.wait_for(task, 2)
            self.assertEqual(json.loads(raw)['phase'], 'stopping')
            self.assertTrue(await asyncio.to_thread(cancelled.wait, 2))
            self.client.send_code_request.assert_not_awaited()
            raw = await asyncio.to_thread(self.bridge.dispatch, str(self.home), 'stop')
            self.assertEqual(json.loads(raw)['phase'], 'offline')
            self.assertFalse(json.loads(raw)['busy'])
            self.client.disconnect.assert_awaited()
            self.bot.research.stop.assert_awaited()
        finally:
            self.bridge.request_stop()
            if task is not None:
                await asyncio.wait_for(task, 2)
            if self.bridge._loop:
                self.bridge._loop.call_soon_threadsafe(self.bridge._loop.stop)
                await asyncio.to_thread(self.bridge._thread.join, 2)
                self.bridge._loop.close()

    async def test_stop_discards_queued_logins_until_disconnect_finishes(self):
        try:
            self.bridge.request_stop()
            with patch.object(self.bridge.importlib, 'import_module', return_value=self.bot), patch.dict(os.environ):
                raw = await asyncio.to_thread(self.bridge.dispatch, str(self.home), 'login', json.dumps(self.values))
                self.assertEqual(json.loads(raw)['phase'], 'stopping')
                self.client.connect.assert_not_awaited()
                await asyncio.to_thread(self.bridge.dispatch, str(self.home), 'stop')
                raw = await asyncio.to_thread(self.bridge.dispatch, str(self.home), 'login', json.dumps(self.values))
                self.assertEqual(json.loads(raw)['phase'], 'code')
            await asyncio.to_thread(self.bridge.dispatch, str(self.home), 'stop')
        finally:
            if self.bridge._loop:
                self.bridge._loop.call_soon_threadsafe(self.bridge._loop.stop)
                await asyncio.to_thread(self.bridge._thread.join, 2)
                self.bridge._loop.close()

    async def test_disconnect_failure_still_stops_the_research_worker(self):
        await self.login()
        self.client.disconnect.side_effect = OSError('PRIVATE_NETWORK_DETAILS')
        await self.bridge._dispatch(self.home, 'stop', {})
        self.bot.research.stop.assert_awaited_once()
        self.assertFalse(self.state()['running'])
        self.assertNotIn('PRIVATE_NETWORK_DETAILS', self.bridge.snapshot())
