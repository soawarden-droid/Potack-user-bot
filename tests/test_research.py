import ast
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from telethon.errors import FloodWaitError, RPCError
from telethon.tl import functions, types

from research import COMMANDS, Research, is_research_command, parse
from research_api import READ_METHODS, ReadOnlyAPI, ResearchError, amount, gift
from research_format import render, units
from research_store import ResearchStore


NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def catalog_gift(identifier=123):
    return types.StarGift(id=identifier, sticker=types.DocumentEmpty(1), stars=100,
                         convert_stars=85, title='Rose <gold>', limited=True,
                         availability_remains=12, availability_total=500, upgrade_stars=0)


def unique(identifier=9, price=100):
    attrs = [types.StarGiftAttributeModel('Gold <model>', types.DocumentEmpty(11), types.StarGiftAttributeRarity(5)),
             types.StarGiftAttributePattern('Stars', types.DocumentEmpty(12), types.StarGiftAttributeRarityRare()),
             types.StarGiftAttributeBackdrop('Blue', 13, 1, 2, 3, 4, types.StarGiftAttributeRarity(20)),
             types.StarGiftAttributeOriginalDetails(types.PeerUser(777), NOW, sender_id=types.PeerUser(888),
                                                     message=types.TextWithEntities('PRIVATE_SENTINEL', []))]
    return types.StarGiftUnique(id=identifier, gift_id=123, title='Rose <gold>', slug=f'Rose-{identifier}', num=identifier,
                               attributes=attrs, availability_issued=150, availability_total=500,
                               resell_amount=[types.StarsAmount(price, 0), types.StarsTonAmount(1500000000)],
                               owner_id=types.PeerUser(555), owner_address='PRIVATE_WALLET_SENTINEL')


def responses():
    saved = types.SavedStarGift(NOW, unique(), can_upgrade=False)
    return {
        'GetStarGiftsRequest': types.payments.StarGifts(10, [catalog_gift()], [], []),
        'GetUniqueStarGiftRequest': types.payments.UniqueStarGift(unique(), [], []),
        'GetSavedStarGiftsRequest': types.payments.SavedStarGifts(20, [saved], [], [], next_offset='private_owned_offset'),
        'GetResaleStarGiftsRequest': types.payments.ResaleStarGifts(40, [unique()], [], [], next_offset='market_offset', attributes=unique().attributes),
        'GetUniqueStarGiftValueInfoRequest': types.payments.UniqueStarGiftValueInfo('USD', 12345, NOW, 100, 250,
                value_is_average=True, last_sale_date=NOW, last_sale_price=9000, floor_price=7000, average_price=8000, listed_count=40),
        'GetStarsStatusRequest': types.payments.StarsStatus(types.StarsAmount(10, -500000000), [], []),
    }


class FakeClient:
    def __init__(self):
        self.requests = []
        self.results = responses()
        self.errors = []
        self.owner_reads = 0

    async def get_me(self):
        self.owner_reads += 1
        return SimpleNamespace(id=42, bot=False, phone='PRIVATE_PHONE_SENTINEL')

    async def __call__(self, request, **kwargs):
        self.requests.append(request)
        if self.errors:
            error = self.errors.pop(0)
            if error:
                raise error
        return self.results[type(request).__name__]


class Event:
    def __init__(self, text, chat=42, sender=42, out=True):
        self.raw_text, self.chat_id, self.sender_id, self.out = text, chat, sender, out
        self.edits = []

    async def edit(self, message, **kwargs):
        self.edits.append(message)


class ParserTests(unittest.TestCase):
    def test_all_commands(self):
        examples = ['/gifts', '/giftinfo 123', '/collectible Rose-9', '/mygifts', '/giftmarket 123',
                    '/giftvalue Rose-9', '/stars', '/giftwatch 123', '/giftstats', '/gifthelp',
                    '/giftsearch Rose', '/giftexport json catalog']
        self.assertEqual({parse(c).name for c in examples}, COMMANDS)
        self.assertEqual(parse('/giftmarket 123 model=11 backdrop=13 sort=number').options['model'], 11)
        self.assertEqual(parse('/giftwatch refresh 123').options, {'refresh': True})
        self.assertEqual(parse('/giftsearch "Rose Gold" rarity<=1').args, ['Rose Gold'])

    def test_reject_invalid_ambiguous_input(self):
        for command in ['/giftinfo 0', '/giftinfo -1', '/giftinfo 9223372036854775808', '/giftinfo 1 extra',
                        '/stars phone=foo', '/mygifts @someone', '/giftmarket 1 sort=profit',
                        '/giftmarket 1 model=abc', '/gifts page=0', '/gifts page=1 page=2',
                        '/giftvalue https://t.me/nft/Rose-9', '/giftsearch Rose rarity<=nan',
                        '/giftsearch Rose rarity<=101', '/giftexport json owned', '/giftexport csv market',
                        '/giftexport csv catalog ../../file', '/gifts search="unterminated',
                        '/giftwatch remove', '/giftmarket 123 next=../../file']:
            with self.subTest(command=command), self.assertRaises(ResearchError):
                parse(command)
        for command in ['/giftship', '/gifts@somebot', 'please /gifts', '.help', 'Qalawa 100']:
            self.assertIsNone(parse(command))

    def test_precise_currency_and_privacy_normalization(self):
        self.assertEqual(amount(types.StarsAmount(10, -500000000)), {'currency': 'Stars', 'amount': '9.5'})
        self.assertEqual(amount(types.StarsTonAmount(1500000000)), {'currency': 'TON', 'amount': '1.5'})
        normalized = json.dumps(gift(unique()))
        for value in ['PRIVATE_SENTINEL', 'PRIVATE_WALLET_SENTINEL', 'owner_id', 'sender_id', 'recipient_id']:
            self.assertNotIn(value, normalized)
        self.assertIn('rarity_permille', normalized)

    def test_long_html_pagination(self):
        lines = ['<b>'+('😀 &amp; ' * 40)+'</b>' for _ in range(60)]
        first, count = render('Long catalog', lines)
        self.assertGreater(count, 1)
        for page in range(1, count+1):
            text, _ = render('Long catalog', lines, page=page)
            self.assertLess(units(text), 4096)
            self.assertTrue(text.endswith('Created by xa'))
            self.assertEqual(text.count('<b>'), text.count('</b>'))
        with self.assertRaises(ValueError):
            render('Long', lines, page=count+1)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = FakeClient()
        self.time = 1000.0
        async def no_sleep(seconds):
            self.time += seconds
        self.sleeps = []
        async def sleep(seconds):
            self.sleeps.append(seconds)
            await no_sleep(seconds)
        self.api = ReadOnlyAPI(self.client, sleep=sleep, clock=lambda: self.time)
        self.engine = Research(self.client, Path(self.temp.name)/'research_data', api=self.api, clock=lambda: self.time)

    async def asyncTearDown(self):
        await self.engine.stop()
        self.temp.cleanup()

    async def command(self, text, **kwargs):
        event = Event(text, **kwargs)
        await self.engine.handle(event)
        self.assertTrue(event.edits, text)
        self.assertTrue(event.edits[-1].endswith('Created by xa'), text)
        self.assertLess(units(event.edits[-1]), 4096)
        return event.edits[-1]

    async def test_all_requested_commands(self):
        for text, expected in [('/gifts', 'Catalog price: 100 Stars'), ('/giftinfo 123', 'Upgrade price: 0 Stars'),
                              ('/collectible Rose-9', 'Gold &lt;model&gt;'), ('/mygifts', 'Telegram-reported total: 20'),
                              ('/giftmarket 123', 'asking prices'), ('/giftvalue Rose-9', 'Last completed resale reported'),
                              ('/stars', 'Balance: 9.5 Stars'), ('/giftwatch 123', 'Watching gift 123'),
                              ('/giftstats', 'Collected market snapshots'), ('/gifthelp', '/giftmarket ID')]:
            with self.subTest(text=text):
                self.assertIn(expected, await self.command(text))
        self.assertEqual({type(r).__name__ for r in self.client.requests}, set(READ_METHODS))

    async def test_authorization_uses_account_id(self):
        for event in [Event('/stars', sender=99), Event('/gifts', out=False), Event('/mygifts', sender=-100)]:
            await self.engine.handle(event)
            self.assertFalse(event.edits)
        self.assertFalse(self.client.requests)
        for text in ['/stars', '/mygifts']:
            self.assertIn('Saved Messages', await self.command(text, chat=7))
        self.assertFalse(self.client.requests)
        self.assertIsNone(self.engine._store)

    async def test_catalog_cached_stale_and_missing(self):
        self.assertIn('Verified live', await self.command('/gifts'))
        self.assertIn('Cached API', await self.command('/gifts'))
        self.assertEqual(len(self.client.requests), 1)
        self.client.errors = [OSError('SECRET_TOKEN'), OSError('SECRET_TOKEN'), OSError('SECRET_TOKEN')]
        text = await self.command('/gifts refresh')
        self.assertIn('STALE', text)
        self.assertNotIn('SECRET_TOKEN', text)
        self.assertIn('not in the returned catalog', await self.command('/giftinfo 999'))

    async def test_flood_wait_and_backoff(self):
        self.client.errors = [FloodWaitError(request=None, capture=7)]
        text = await self.command('/stars')
        self.assertIn('Balance: 9.5', text)
        self.assertTrue(any(seconds >= 7 for seconds in self.sleeps))
        self.assertEqual(len(self.client.requests), 2)
        self.client.errors = [OSError('SECRET'), None]
        await self.command('/stars')
        self.assertTrue(any(seconds >= 2 for seconds in self.sleeps))

    async def test_unavailable_method_and_errors(self):
        self.api.allowed['GetUniqueStarGiftValueInfoRequest'] = None
        self.assertIn('unavailable in the installed', await self.command('/giftvalue Rose-9'))
        self.client.errors = [RPCError(request=None, message='SECRET', code=403)]
        text = await self.command('/stars')
        self.assertIn('does not permit', text)
        self.assertNotIn('SECRET', text)
        self.client.results['GetUniqueStarGiftRequest'] = object()
        self.assertIn('unsupported response', await self.command('/collectible Rose-9'))

    async def test_server_reports_unavailable_method(self):
        self.client.errors = [RPCError(request=None, message='METHOD_INVALID', code=400)]
        self.assertIn('method is unavailable', await self.command('/giftvalue Rose-9'))

    async def test_opaque_owned_pagination(self):
        import re
        text = await self.command('/mygifts')
        token = re.search(r'next=(p[0-9a-f]{12})', text).group(1)
        self.assertNotIn('private_owned_offset', text)
        await self.command('/mygifts next='+token)
        self.assertEqual(self.client.requests[-1].offset, 'private_owned_offset')
        self.assertIsInstance(self.client.requests[-1].peer, types.InputPeerSelf)
        self.assertTrue(self.client.requests[-1].exclude_hosted)
        self.time += 3601
        self.assertIn('expired', await self.command('/mygifts next='+token))

    async def test_market_pagination_preserves_filters(self):
        import re
        text = await self.command('/giftmarket 123 model=11 pattern=12 backdrop=13 currency=stars sort=number')
        req = self.client.requests[-1]
        self.assertTrue(req.sort_by_num)
        self.assertIsNone(req.sort_by_price)
        self.assertTrue(req.stars_only)
        self.assertEqual(len(req.attributes), 3)
        token = re.search(r'next=(p[0-9a-f]{12})', text).group(1)
        await self.command('/giftmarket 123 next='+token)
        req = self.client.requests[-1]
        self.assertEqual(req.offset, 'market_offset')
        self.assertEqual(len(req.attributes), 3)
        self.assertTrue(req.sort_by_num)
        self.assertIn('original filters', await self.command('/giftmarket 123 next='+token+' sort=price'))
        self.assertIn('another gift', await self.command('/giftmarket 124 next='+token))

    async def test_actual_price_comparison_and_watch_refresh(self):
        self.assertIn('Two collected snapshots', await self.command('/giftwatch 123'))
        self.client.results['GetResaleStarGiftsRequest'] = types.payments.ResaleStarGifts(30, [unique(price=120)], [], [])
        self.time += 901
        await self.engine.refresh_watches()
        text = await self.command('/giftwatch 123')
        self.assertIn('100 → 120', text)
        self.assertIn('change +20', text)
        self.assertIn('not completed sales or profit', text)
        count = len(self.client.requests)
        await self.engine.refresh_watches()
        self.assertEqual(len(self.client.requests), count)
        self.assertIn('Gift 123', await self.command('/giftwatch list'))
        await self.command('/giftwatch remove 123')
        self.assertFalse(self.engine.store.watches())

    async def test_search_and_export_local_only(self):
        await self.command('/gifts')
        await self.command('/collectible Rose-9')
        await self.command('/giftmarket 123')
        count = len(self.client.requests)
        self.assertIn('Local matches: 1', await self.command('/giftsearch Rose model=Gold rarity<=1'))
        self.assertIn('Local matches: 1', await self.command('/giftsearch Rose rarity=rare'))
        self.assertIn('Local matches: 0', await self.command('/giftsearch Rose rarity<=0.01'))
        for text in ['/giftexport json catalog', '/giftexport csv catalog', '/giftexport json market 123', '/giftexport csv market 123']:
            self.assertIn('No file was uploaded', await self.command(text))
        self.assertEqual(len(self.client.requests), count)
        exported = list((Path(self.temp.name)/'research_data/exports').iterdir())
        self.assertEqual(len(exported), 4)
        for path in exported:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for sentinel in ['PRIVATE_PHONE_SENTINEL', 'PRIVATE_WALLET_SENTINEL', 'PRIVATE_SENTINEL']:
                self.assertNotIn(sentinel, path.read_text())
        self.assertEqual(self.engine.store.directory.stat().st_mode & 0o777, 0o700)

    async def test_export_empty_and_csv_formulas(self):
        self.assertIn('No collected data', await self.command('/giftexport json catalog'))
        path = self.engine.store.export('csv', 'catalog', [{'title': '=HYPERLINK("evil")'}])
        self.assertIn("'=HYPERLINK", path.read_text())

    async def test_no_fake_resale_when_missing(self):
        self.client.results['GetUniqueStarGiftValueInfoRequest'] = types.payments.UniqueStarGiftValueInfo('USD', 0, NOW, 0, 0)
        text = await self.command('/giftvalue Rose-9')
        self.assertIn('Completed resale information: Not returned', text)
        self.assertNotIn('Last completed resale reported', text)
        self.assertIn('Estimated value: 0 USD minor units', text)

    async def test_read_only_boundary_blocks_mutations_and_overrides_peer(self):
        with self.assertRaises(ResearchError):
            await self.api.read('TransferStarGiftRequest')
        with self.assertRaises(ResearchError):
            await self.api.read('SendStarsFormRequest')
        self.assertFalse(self.client.requests)
        await self.api.read('GetStarsStatusRequest', peer=types.InputPeerUser(99, 0))
        self.assertIsInstance(self.client.requests[-1].peer, types.InputPeerSelf)
        for req in self.client.requests:
            self.assertIn(type(req).__name__, READ_METHODS)

    async def test_all_other_payment_methods_are_blocked(self):
        for name in dir(functions.payments):
            if name.endswith('Request') and name not in READ_METHODS:
                with self.subTest(name=name), self.assertRaises(ResearchError):
                    await self.api.read(name)
        self.assertFalse(self.client.requests)

    async def test_stale_watch_does_not_create_fake_snapshot(self):
        await self.command('/giftwatch 123')
        count = self.engine.store.stats()['snapshots']
        self.time += 901
        self.client.errors = [OSError('SECRET'), OSError('SECRET'), OSError('SECRET')]
        await self.engine.refresh_watches()
        self.assertEqual(self.engine.store.stats()['snapshots'], count)
        self.assertTrue(self.engine.store.watches()[0]['last_error'])
        self.assertIn('last refresh failed', await self.command('/giftwatch list'))

    async def test_long_catalog_command_and_invalid_display_page(self):
        self.client.results['GetStarGiftsRequest'] = types.payments.StarGifts(1, [catalog_gift(i) for i in range(1, 80)], [], [])
        text = await self.command('/gifts')
        self.assertIn('Page 1 /', text)
        self.assertNotIn('Page 1 / 1', text)
        self.assertIn('Page 2 /', await self.command('/gifts page=2'))
        self.assertIn('Page must be between', await self.command('/gifts page=1000'))
        self.assertEqual(len(self.client.requests), 1)

    async def test_signed_backdrop_filter(self):
        await self.command('/giftmarket 123 backdrop=0')
        self.assertEqual(self.client.requests[-1].attributes[0].backdrop_id, 0)
        await self.command('/giftmarket 123 backdrop=-5')
        self.assertEqual(self.client.requests[-1].attributes[0].backdrop_id, -5)

    async def test_watchlist_capacity(self):
        for gift_id in range(1, 26):
            self.assertTrue(self.engine.store.watch(gift_id))
        self.assertFalse(self.engine.store.watch(26))
        self.assertTrue(self.engine.store.watch(25))
        self.assertEqual(len(self.engine.store.watches()), 25)

    async def test_worker_lifecycle(self):
        await self.engine.start()
        task = self.engine.worker
        await self.engine.start()
        self.assertIs(self.engine.worker, task)
        await self.engine.stop()
        self.assertTrue(task.done())
        self.assertFalse(self.client.requests)


class ExistingIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_existing_registration_and_formatter_with_offline_config(self):
        class OfflineClient:
            def __init__(self, *args, **kwargs): self.handlers = []; self.parse_mode = None
            def on(self, event):
                def register(fn): self.handlers.append(fn); return fn
                return register
        root = Path(__file__).resolve().parents[1]
        original = Path.open
        def opened(path, *args, **kwargs):
            if path.name == 'potack_private.json':
                return io.StringIO(json.dumps({'api_id': 1, 'api_hash': 'TEST_ONLY', 'OPENROUTER_API_KEY': 'TEST_ONLY', 'session_name': 'test_only'}))
            return original(path, *args, **kwargs)
        with patch('telethon.TelegramClient', OfflineClient), patch.object(Path, 'open', opened), patch('os.path.exists', return_value=False):
            spec = importlib.util.spec_from_file_location('potack_offline_test', root/'main.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        names = {h.__name__ for h in module.client.handlers}
        self.assertTrue({'ping', 'status', 'bold_mode', 'italic_mode', 'underline_mode', 'strike_mode', 'code_mode',
                         'format_status', 'format_off', 'automatic_formatter', 'ai_command', 'xa_search',
                         'search_command', 'save_info', 'get_info', 'forget_info', 'notes', 'show_id', 'me',
                         'about', 'clear_command', 'qalawa', 'help_command', 'gift_research'} <= names)
        self.assertEqual(module.client.parse_mode, 'html')
        module.data = {'notes': {}, 'format_modes': {'42': {'bold': True}}}
        with tempfile.TemporaryDirectory() as temporary:
            module.DATA_FILE = str(Path(temporary)/'potack_data.json')
            event = Event('/gifts')
            await module.automatic_formatter(event)
            self.assertFalse(event.edits)
            event = Event('Qalawa 100')
            await module.automatic_formatter(event)
            self.assertFalse(event.edits)
            event = Event('hello <world>')
            await module.automatic_formatter(event)
            self.assertIn('<b>hello &lt;world&gt;</b>', event.edits[-1])
            event = Event('.ping')
            await module.ping(event)
            self.assertIn('Created by xa', event.edits[-1])
            event = Event('.bold off')
            await module.set_format_mode(event, 'bold', False)
            self.assertFalse(json.loads(Path(module.DATA_FILE).read_text())['format_modes']['42']['bold'])
        import re
        with tempfile.TemporaryDirectory() as temporary:
            module.DATA_FILE = str(Path(temporary)/'potack_data.json')
            event = Event('.save title hello <world>')
            event.pattern_match = re.match(r'^\.save ([^ ]+) (.+)$', event.raw_text)
            await module.save_info(event)
            self.assertEqual(module.data['notes']['title'], 'hello <world>')
            event = Event('.get title')
            event.pattern_match = re.match(r'^\.get ([^ ]+)$', event.raw_text)
            await module.get_info(event)
            self.assertIn('hello &lt;world&gt;', event.edits[-1])
            event = Event('.forget title')
            event.pattern_match = re.match(r'^\.forget ([^ ]+)$', event.raw_text)
            await module.forget_info(event)
            self.assertNotIn('title', module.data['notes'])
            self.assertEqual(json.loads(Path(module.DATA_FILE).read_text())['notes'], {})
            event = Event('.help')
            await module.help_command(event)
            self.assertIn('Qalawa 100', event.edits[-1])
            self.assertIn('/gifthelp', event.edits[-1])
        self.assertIsNone(module.research._store)

    def test_only_explicit_read_requests_in_research_modules(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ['research.py', 'research_api.py', 'research_store.py', 'research_format.py']:
            tree = ast.parse((root/filename).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr.endswith('Request'):
                    self.assertIn(node.attr, READ_METHODS)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, {'send_file', 'send_message', 'delete_messages', 'delete'})
        self.assertEqual(len(READ_METHODS), 6)


if __name__ == '__main__':
    unittest.main()
