import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from telethon.errors import FloodWaitError
from cleanup import Cleanup, parse_command, branded


def message(identifier, sender=42, action=None):
    return SimpleNamespace(id=identifier, sender_id=sender, action=action)


class Event:
    def __init__(self, text, identifier=1000, chat=7, reply=None):
        self.raw_text = text
        self.id = identifier
        self.chat_id = chat
        self.reply_to_msg_id = reply
        self.edits = []
        self.removed = False

    async def edit(self, text, **kwargs):
        self.edits.append(text)

    async def delete(self):
        self.removed = True


class Client:
    def __init__(self, messages):
        self.messages = {m.id: m for m in messages}
        self.deleted = []
        self.refuse = set()
        self.flood_once = False
        self.on_delete = None
        self.pages = 0

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_messages(self, chat, ids=None, limit=None, max_id=None, min_id=None):
        if ids is not None:
            return [self.messages.get(i) for i in ids]
        self.pages += 1
        return sorted((m for m in self.messages.values() if min_id < m.id < max_id), key=lambda m: m.id, reverse=True)[:limit]

    async def delete_messages(self, chat, ids, **kwargs):
        if self.flood_once:
            self.flood_once = False
            raise FloodWaitError(request=None, capture=0)
        self.deleted.append(list(ids))
        for i in ids:
            if i not in self.refuse:
                self.messages.pop(i, None)
        if self.on_delete:
            self.on_delete()


class Parsing(unittest.TestCase):
    def test_unambiguous(self):
        for value in ['Qalawa 0', 'Qalawa -1', 'Qalawa 10001', 'Qalawa 1 more', 'Qalawa preview 2 more', 'Qalawa 1.0']:
            self.assertEqual(parse_command(value), ('invalid', None))
        self.assertIsNone(parse_command('qalawacity'))
        self.assertIsNone(parse_command('please Qalawa 100'))
        self.assertEqual(parse_command('QALAWA preview 100'), ('preview', 100))
        self.assertEqual(parse_command('blue moon'), ('clean', None))
        self.assertEqual(parse_command('Qalawa autodelete off'), ('setting', 0))
        self.assertEqual(branded('Hello'), 'Hello\n\nCreated by xa')


class Behavior(unittest.IsolatedAsyncioTestCase):
    def build(self, messages):
        c = Client(messages)
        engine = Cleanup(c, {}, lambda: None)
        async def fast_wait(job, seconds):
            await asyncio.sleep(0)
            if job.cancel.is_set():
                from cleanup import Stopped
                raise Stopped
        engine.wait = fast_wait
        return c, engine

    async def test_ownership_count_and_exclusions(self):
        c, engine = self.build([message(i, 42 if i % 2 else 99) for i in range(1, 210)] + [message(1000), message(1200)])
        event = Event('Qalawa 100')
        await engine.handle(event)
        deleted = [i for batch in c.deleted for i in batch]
        self.assertEqual(len(deleted), 100)
        self.assertTrue(all(i % 2 for i in deleted))
        self.assertTrue(all(len(batch) <= 50 for batch in c.deleted))
        self.assertIn(1000, c.messages)
        self.assertIn(1200, c.messages)
        self.assertIn('Deleted: 100 messages', event.edits[-1])
        self.assertTrue(event.edits[-1].endswith('Created by xa'))
        self.assertFalse(engine.jobs)

    async def test_preview_and_reply_range(self):
        c, engine = self.build([message(i) for i in range(1, 12)])
        preview = Event('Qalawa preview 100', identifier=12, reply=8)
        await engine.handle(preview)
        self.assertFalse(c.deleted)
        self.assertIn('Eligible: 4 / 100', preview.edits[-1])
        await engine.handle(Event('Qalawa', identifier=12, reply=8))
        self.assertEqual(c.deleted, [[11, 10, 9, 8]])
        self.assertIn(7, c.messages)

    async def test_silent_failures(self):
        c, engine = self.build([message(1), message(2)])
        c.refuse = {2}
        event = Event('Qalawa 2')
        await engine.handle(event)
        self.assertIn('Deleted: 1 messages', event.edits[-1])
        self.assertIn('Failed: 1', event.edits[-1])
        self.assertIn('partially completed', event.edits[-1])

    async def test_cancel_after_inflight_batch(self):
        c, engine = self.build([message(i) for i in range(1, 201)])
        event = Event('Qalawa 200')
        c.on_delete = lambda: engine.jobs[7].cancel.set()
        await engine.handle(event)
        self.assertEqual(len(c.deleted), 1)
        self.assertIn('Cleanup cancelled', event.edits[-1])
        self.assertIn('Deleted: 50 messages', event.edits[-1])
        self.assertIn('Not attempted: 50', event.edits[-1])

    async def test_overlap(self):
        c, engine = self.build([message(1)])
        entered = asyncio.Event()
        release = asyncio.Event()
        original = c.get_me
        async def blocked():
            entered.set()
            await release.wait()
            return await original()
        c.get_me = blocked
        first = asyncio.create_task(engine.handle(Event('Qalawa 1')))
        await entered.wait()
        second = Event('Qalawa 1', identifier=1001)
        await engine.handle(second)
        self.assertIn('already running', second.edits[-1])
        await engine.handle(Event('Qalawa cancel', identifier=1002))
        release.set()
        await first
        self.assertFalse(c.deleted)
        self.assertFalse(engine.jobs)

    async def test_flood_wait(self):
        c, engine = self.build([message(1)])
        c.flood_once = True
        event = Event('Qalawa 1')
        await engine.handle(event)
        self.assertTrue(any('Potack is waiting' in text for text in event.edits))
        self.assertIn('Deleted: 1 messages', event.edits[-1])

    async def test_service_and_channel_authors_excluded(self):
        c, engine = self.build([message(1), message(2, action=object()), message(3, sender=-100)])
        await engine.handle(Event('Qalawa'))
        self.assertEqual(c.deleted, [[1]])

    async def test_recheck_author(self):
        c, engine = self.build([message(1)])
        original = c.get_messages
        async def changed(*args, **kwargs):
            if kwargs.get('ids'):
                c.messages[1].sender_id = 99
            return await original(*args, **kwargs)
        c.get_messages = changed
        event = Event('Qalawa 1')
        await engine.handle(event)
        self.assertFalse(c.deleted)
        self.assertIn('Skipped: 1', event.edits[-1])

    async def test_verification_failure_is_unconfirmed(self):
        c, engine = self.build([message(1)])
        original = c.get_messages
        async def failure(*args, **kwargs):
            if kwargs.get('ids') and c.deleted:
                raise OSError('offline')
            return await original(*args, **kwargs)
        c.get_messages = failure
        event = Event('Qalawa 1')
        await engine.handle(event)
        self.assertIn('Deleted: 0 messages', event.edits[-1])
        self.assertIn('Unconfirmed: 1', event.edits[-1])

    async def test_autodelete_setting_and_task(self):
        c, engine = self.build([])
        await engine.handle(Event('Qalawa autodelete 1'))
        self.assertEqual(engine.settings['final_status_seconds'], 1)
        event = Event('Qalawa 1')
        await engine.handle(event)
        self.assertEqual(len(engine.timers), 1)
        for task in list(engine.timers):
            task.cancel()
        await asyncio.gather(*engine.timers, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
