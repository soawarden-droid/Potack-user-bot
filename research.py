"""Read-only Gifts/Stars commands, preserving the existing Potack command handlers."""
import asyncio
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import shlex
import time

from telethon.errors import FloodWaitError, MessageNotModifiedError
from telethon.tl import types

from research_api import ReadOnlyAPI, ResearchError, normalize
from research_format import escape, field, gift_lines, render, timestamp
from research_store import ResearchStore, comparison

COMMANDS = {'gifts', 'giftinfo', 'collectible', 'mygifts', 'giftmarket', 'giftvalue',
            'stars', 'giftwatch', 'giftstats', 'gifthelp', 'giftsearch', 'giftexport'}
PAGE_SIZE = 8
WATCH_INTERVAL = 900
CACHE_TTL = 600
MARKET_TTL = 60
HELP = [
    '<code>/gifts</code> · gift catalog; add <code>page=2</code>, <code>search="Rose"</code>, or <code>refresh</code>.',
    '<code>/giftinfo ID</code> · gift type metadata.',
    '<code>/collectible SLUG</code> · collectible attributes and rarity.',
    '<code>/mygifts</code> · your gifts, in Saved Messages.',
    '<code>/giftmarket ID</code> · resale asks and available attribute filters.',
    '<code>/giftvalue SLUG</code> · Telegram value estimates and reported sale data.',
    '<code>/stars</code> · your Stars balance, in Saved Messages.',
    '<code>/giftwatch ID</code> · local watch; refresh every 15 minutes while Potack runs.',
    '<code>/giftwatch list</code> · watches; <code>/giftwatch refresh ID</code> · refresh; <code>/giftwatch remove ID</code> · remove locally.',
    '<code>/giftstats</code> · local counts and snapshot changes.',
    '<code>/giftsearch "Rose" model="Gold" rarity&lt;=1</code> · search metadata already cached locally (rarity is a percent).',
    '<code>/giftexport json catalog</code> or <code>/giftexport csv market ID</code> · local exports only.',
    'Market options: <code>sort=price|number|recent</code>, <code>currency=stars|all</code>, <code>model=ID</code>, <code>pattern=ID</code>, <code>backdrop=ID</code>.',
    'Catalog options: <code>available=true|false</code>, <code>upgradable=true|false</code>.',
    'Local search options: <code>model=NAME</code>, <code>pattern=NAME</code>, <code>backdrop=NAME</code>, <code>rarity=rare|epic|legendary|uncommon</code>, <code>rarity&lt;=PERCENT</code>.',
    'Use the returned <code>next=p…</code> token for owned-gift and market pages; tokens expire after one hour.',
    'Use <code>page=N</code> for long catalog, detail, search, help, and stats messages. Prices are labeled as asking prices or Telegram-reported historical sales.',
    'Missing values mean Telegram did not return the data. Cached results carry their original timestamps. No purchases, sales, upgrades, transfers, or wallet connections.',
]


@dataclass
class Command:
    name: str
    args: list
    options: dict


def is_research_command(text):
    match = re.match(r'^/([a-z]+)(?:\s|$)', text.strip(), re.I)
    return bool(match and match.group(1).casefold() in COMMANDS)


def positive_id(text):
    if not re.fullmatch(r'[0-9]{1,19}', text) or not 0 < int(text) < 2**63:
        raise ResearchError('Gift ID must be a positive Telegram 64-bit integer.')
    return int(text)


def parse(text):
    if not is_research_command(text):
        return None
    try:
        parts = shlex.split(text.strip())
    except ValueError:
        raise ResearchError('Check the quotation marks. Use /gifthelp for examples.') from None
    name = parts[0][1:].casefold()
    options, args = {}, []
    for part in parts[1:]:
        if part == 'refresh':
            if 'refresh' in options:
                raise ResearchError('The refresh option was supplied twice.')
            options['refresh'] = True
        elif part.startswith('rarity<='):
            key, value = 'rarity_max', part[8:]
            if key in options:
                raise ResearchError('The rarity filter was supplied twice.')
            options[key] = value
        elif '=' in part:
            key, value = part.split('=', 1)
            if key in options:
                raise ResearchError('An option was supplied twice.')
            options[key] = value
        else:
            args.append(part)
    allowed = {
        'gifts': {'page', 'search', 'available', 'upgradable', 'refresh'},
        'giftinfo': {'page', 'refresh'}, 'collectible': {'page', 'refresh'},
        'mygifts': {'next', 'page', 'refresh'},
        'giftmarket': {'next', 'page', 'sort', 'currency', 'model', 'pattern', 'backdrop', 'refresh'},
        'giftvalue': {'page', 'refresh'}, 'stars': set(),
        'giftwatch': {'page', 'refresh'}, 'giftstats': {'page'}, 'gifthelp': {'page'},
        'giftsearch': {'page', 'model', 'pattern', 'backdrop', 'rarity', 'rarity_max'},
        'giftexport': set(),
    }
    if set(options)-allowed[name]:
        raise ResearchError('Unknown option for this command. Use /gifthelp.')
    if 'page' in options:
        if not re.fullmatch(r'[0-9]{1,4}', options['page']) or not 1 <= int(options['page']) <= 1000:
            raise ResearchError('Page must be an integer from 1 to 1000.')
        options['page'] = int(options['page'])
    for key in ['available', 'upgradable']:
        if key in options and options[key] not in {'true', 'false'}:
            raise ResearchError(f'{key} must be true or false.')
    if name in {'giftinfo', 'giftmarket'}:
        if len(args) != 1:
            raise ResearchError(f'Use /{name} ID. See /gifthelp for options.')
        args = [positive_id(args[0])]
    elif name in {'collectible', 'giftvalue'}:
        if len(args) != 1 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}-[1-9][0-9]{0,19}', args[0]):
            raise ResearchError('Use a collectible slug such as Plume-123, not a URL or username.')
    elif name == 'giftwatch':
        if not args:
            args = ['list']
        elif args == ['list']:
            pass
        elif len(args) == 2 and args[0] == 'remove':
            args = ['remove', positive_id(args[1])]
        elif len(args) == 1:
            args = [positive_id(args[0])]
        else:
            raise ResearchError('Use /giftwatch ID, /giftwatch refresh ID, /giftwatch list, or /giftwatch remove ID.')
    elif name == 'giftexport':
        if len(args) not in {2, 3} or args[0] not in {'json', 'csv'} or args[1] not in {'catalog', 'market'}:
            raise ResearchError('Use /giftexport json|csv catalog or /giftexport json|csv market ID.')
        if args[1] == 'market' and len(args) == 3:
            args[2] = positive_id(args[2])
        elif args[1] == 'market' or len(args) != 2:
            raise ResearchError('Market export requires a gift ID. Catalog export takes no ID.')
    elif name == 'giftsearch':
        if not args and not options.keys()-{'page'}:
            raise ResearchError('Supply a search term or attribute filter. Use /gifthelp.')
    elif args:
        raise ResearchError('Unexpected argument. Use /gifthelp.')
    if name == 'giftmarket':
        if options.get('sort', 'price') not in {'price', 'number', 'recent'}:
            raise ResearchError('Market sort must be price, number, or recent.')
        if options.get('currency', 'all') not in {'stars', 'all'}:
            raise ResearchError('Market currency must be stars or all.')
        for key in ['model', 'pattern']:
            if key in options:
                options[key] = positive_id(options[key])
        if 'backdrop' in options:
            if not re.fullmatch(r'-?[0-9]{1,10}', options['backdrop']) or not -2**31 <= int(options['backdrop']) < 2**31:
                raise ResearchError('Backdrop ID must fit a Telegram signed 32-bit integer.')
            options['backdrop'] = int(options['backdrop'])
    if 'next' in options and not re.fullmatch(r'p[0-9a-f]{12}', options['next']):
        raise ResearchError('Invalid next-page token. Use the token returned by Potack.')
    if 'rarity' in options and options['rarity'] not in {'uncommon', 'rare', 'epic', 'legendary'}:
        raise ResearchError('Rarity category must be uncommon, rare, epic, or legendary.')
    if 'rarity_max' in options:
        try:
            value = Decimal(options['rarity_max'])
            if not value.is_finite() or not 0 <= value <= 100:
                raise ValueError
        except (ValueError, InvalidOperation):
            raise ResearchError('Rarity percentage must be a number from 0 to 100.') from None
    return Command(name, args, options)


class Research:
    def __init__(self, client, directory, api=None, clock=time.time):
        self.client = client
        self.directory = Path(directory)
        self.api = api or ReadOnlyAPI(client)
        self.clock = clock
        self._store = None
        self.fetch_lock = asyncio.Lock()
        self.worker = None
        self.active = set()

    @property
    def store(self):
        if self._store is None:
            self._store = ResearchStore(self.directory, self.clock)
        return self._store

    async def start(self):
        if self.worker is None:
            self.worker = asyncio.create_task(self.watch_loop())

    async def stop(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        if self._store:
            self._store.close()
            self._store = None

    async def reply(self, event, title, lines, source=None, page=1):
        try:
            message, pages = render(title, lines, source, page)
        except ValueError as error:
            message, pages = render('Pagination', [escape(str(error))])
        for attempt in range(3):
            try:
                await event.edit(message, parse_mode='html', link_preview=False)
                return
            except MessageNotModifiedError:
                return
            except FloodWaitError as error:
                await self.api.sleep(error.seconds)
            except Exception:
                # No chat/account details or raw exceptions are logged.
                return

    async def fetch(self, key, method, kwargs, ttl, refresh=False, status=None, index=False):
        # Serialize fetch/cache writes so concurrent commands don't duplicate snapshots.
        async with self.fetch_lock:
            cached = self.store.get(key)
            if cached and not refresh and self.clock()-cached[1] < ttl:
                return cached[0], cached[1], 'cached', None
            try:
                response = await self.api.read(method, status=status, **kwargs)
                payload = normalize(method, response)
            except ResearchError as error:
                if cached:
                    return cached[0], cached[1], 'stale', str(error)
                raise
            except Exception:
                if cached:
                    return cached[0], cached[1], 'stale', 'The current response was not readable.'
                raise ResearchError('Telegram returned an unsupported response. No result was invented.') from None
            fetched = self.store.put(key, payload)
            if method == 'GetStarGiftsRequest':
                self.store.index_gifts(payload['gifts'], fetched, replace_catalog=True)
            elif index and method == 'GetUniqueStarGiftRequest':
                self.store.index_gifts([payload], fetched)
            elif method == 'GetResaleStarGiftsRequest':
                self.store.index_gifts(payload['gifts'], fetched)
                self.store.snapshot(key, kwargs['gift_id'], payload, fetched)
            return payload, fetched, 'live', None

    def source(self, result):
        _, fetched, state, error = result
        label = {'live': 'Verified live API result', 'cached': 'Cached API result', 'stale': 'STALE cached result; live refresh failed'}[state]
        return label + ' · fetched ' + timestamp(fetched) + ('. '+error if error else '')

    def market_query(self, gift_id, options=None, offset=''):
        options = options or {}
        params = {'gift_id': gift_id, 'offset': offset, 'limit': PAGE_SIZE, 'attributes_hash': 0 if not offset else None}
        sort = options.get('sort', 'price')
        params.update(sort_by_price=True if sort == 'price' else None, sort_by_num=True if sort == 'number' else None,
                      stars_only=True if options.get('currency') == 'stars' else None)
        attributes = []
        for kind, cls, field_name in [('model', types.StarGiftAttributeIdModel, 'document_id'),
                                      ('pattern', types.StarGiftAttributeIdPattern, 'document_id'),
                                      ('backdrop', types.StarGiftAttributeIdBackdrop, 'backdrop_id')]:
            if kind in options:
                attributes.append(cls(**{field_name: options[kind]}))
        if attributes:
            params['attributes'] = attributes
        identity = {'id': gift_id, 'offset': offset, 'sort': sort, 'currency': options.get('currency', 'all'),
                    **{k: options[k] for k in ['model', 'pattern', 'backdrop'] if k in options}}
        return 'market:' + json.dumps(identity, sort_keys=True, separators=(',', ':')), params

    async def market(self, gift_id, options=None, offset='', status=None, refresh=False):
        key, params = self.market_query(gift_id, options, offset)
        return await self.fetch(key, 'GetResaleStarGiftsRequest', params, MARKET_TTL, refresh, status)

    def matching(self, item, query, options):
        terms = ' '.join([str(item.get('title') or ''), str(item['id']), str(item.get('slug') or ''),
                          *[a['name'] for a in item.get('attributes', [])]]).casefold()
        if query.casefold() not in terms:
            return False
        attributes = item.get('attributes', [])
        for kind in ['model', 'pattern', 'backdrop']:
            if kind in options and not any(a['kind'] == kind and options[kind].casefold() in a['name'].casefold() for a in attributes):
                return False
        if 'rarity' in options and not any(a.get('rarity_category') == options['rarity'] for a in attributes):
            return False
        if 'rarity_max' in options and not any(a.get('rarity_permille') is not None and Decimal(a['rarity_permille'])/10 <= Decimal(options['rarity_max']) for a in attributes):
            return False
        return True

    async def handle(self, event):
        text = event.raw_text or ''
        if not is_research_command(text) or not getattr(event, 'out', False):
            return
        try:
            owner = await self.api.owner()
        except ResearchError:
            # Fail closed: never respond to an unverified sender.
            return
        if event.sender_id != owner:
            return
        try:
            cmd = parse(text)
            if cmd.name in {'stars', 'mygifts'} and event.chat_id != owner:
                await self.reply(event, 'Private account research', [f'Use <code>/{cmd.name}</code> in Saved Messages to keep account data private.'])
                return
            if event.chat_id in self.active:
                await self.reply(event, 'Research in progress', ['Wait for the current research command in this chat to finish.'])
                return
            self.active.add(event.chat_id)
            try:
                async def waiting(message):
                    await self.reply(event, 'Research is waiting', [escape(message, 400)])
                await self.execute(event, cmd, waiting)
            finally:
                self.active.discard(event.chat_id)
        except ResearchError as error:
            await self.reply(event, 'Research unavailable', [escape(str(error), 420), 'Use <code>/gifthelp</code> for command examples.'])
        except Exception:
            await self.reply(event, 'Research unavailable', ['Potack could not complete this local research operation. No result was invented.'])

    async def execute(self, event, cmd, waiting):
        name, args, options = cmd.name, cmd.args, cmd.options
        page = options.get('page', 1)
        refresh = options.get('refresh', False)
        if name == 'gifthelp':
            await self.reply(event, 'Research commands', HELP, page=page)
        elif name in {'gifts', 'giftinfo'}:
            result = await self.fetch('catalog', 'GetStarGiftsRequest', {'hash': 0}, CACHE_TTL, refresh, waiting)
            items = result[0]['gifts']
            if name == 'giftinfo':
                items = [g for g in items if g['id'] == args[0]]
                if not items:
                    await self.reply(event, 'Gift type not found', ['This gift ID was not in the returned catalog. Sold-out types may be omitted by Telegram.'], self.source(result))
                    return
            else:
                if 'search' in options:
                    items = [g for g in items if self.matching(g, options['search'], {})]
                if 'available' in options:
                    items = [g for g in items if (not bool(g.get('sold_out'))) == (options['available'] == 'true')]
                if 'upgradable' in options:
                    items = [g for g in items if (g.get('upgrade_stars') is not None) == (options['upgradable'] == 'true')]
            lines = [f'Gift types shown: {len(items)}']
            for item in items:
                lines += [''] + gift_lines(item, full=name == 'giftinfo')
            lines += ['', 'Not marked sold out does not guarantee purchase eligibility.', 'Use page=N to view the next message page.']
            await self.reply(event, 'Gift catalog' if name == 'gifts' else 'Gift metadata', lines, self.source(result), page)
        elif name == 'collectible':
            result = await self.fetch('collectible:'+args[0], 'GetUniqueStarGiftRequest', {'slug': args[0]}, CACHE_TTL, refresh, waiting, index=True)
            await self.reply(event, 'Collectible', gift_lines(result[0], full=True), self.source(result), page)
        elif name == 'stars':
            response = await self.api.read('GetStarsStatusRequest', status=waiting, peer=types.InputPeerSelf())
            payload = normalize('GetStarsStatusRequest', response)
            await self.reply(event, 'Your Stars', ['Balance: '+escape(payload['balance']['amount'])+' Stars'], 'Verified live API result · fetched '+timestamp(self.clock()))
        elif name == 'mygifts':
            scope = 'owned:'+hashlib.sha256(str(await self.api.owner()).encode()).hexdigest()[:16]
            offset = ''
            if 'next' in options:
                token = self.store.resolve(options['next'], scope)
                if token is None:
                    raise ResearchError('This owned-gifts page token expired or belongs to another account. Start with /mygifts.')
                offset = token['offset']
            key = scope+':'+hashlib.sha256(offset.encode()).hexdigest()
            result = await self.fetch(key, 'GetSavedStarGiftsRequest', {'peer': types.InputPeerSelf(), 'offset': offset, 'limit': PAGE_SIZE, 'exclude_hosted': True}, 60, refresh, waiting)
            payload = result[0]
            lines = [f'Telegram-reported total: {payload["count"]} · This API page: {len(payload["gifts"])}']
            for item in payload['gifts']:
                lines += ['']+gift_lines(item)
            if payload.get('next_offset') and payload['next_offset'] != offset:
                token = self.store.token(scope, {'offset': payload['next_offset']})
                lines += ['', f'Next API page: <code>/mygifts next={token}</code>']
            await self.reply(event, 'Your received and owned gifts', lines, self.source(result), page)
        elif name == 'giftmarket':
            gift_id = args[0]
            scope = 'market:'+str(gift_id)
            offset = ''
            if 'next' in options:
                token = self.store.resolve(options['next'], scope)
                if token is None:
                    raise ResearchError('This market page token expired or belongs to another gift. Start with /giftmarket ID.')
                selected = token['options']
                # Prevent silently changing filters halfway through an API page chain.
                for key in ['sort', 'currency', 'model', 'pattern', 'backdrop']:
                    if key in options and options[key] != selected.get(key):
                        raise ResearchError('Page tokens keep their original filters. Start a new market query to change filters.')
                options = {**selected, **{k: v for k, v in options.items() if k in {'page', 'refresh'}}}
                offset = token['offset']
            result = await self.market(gift_id, options, offset, waiting, refresh)
            payload = result[0]
            lines = [f'Telegram listing count for this query: {payload["count"]}',
                     f'Sampled API page: {len(payload["gifts"])} listings · Sort: {escape(options.get("sort", "price"))}',
                     'Listing prices are asking prices; they do not prove a completed sale.']
            for item in payload['gifts']:
                lines += ['']+gift_lines(item)
            applied = ', '.join(f'{k}={options[k]}' for k in ['currency', 'model', 'pattern', 'backdrop'] if k in options)
            lines += ['', 'Applied filters: '+escape(applied or 'none'), 'Available filters returned by Telegram:']
            for attr in payload.get('attributes', []):
                rarity = f'{Decimal(attr["rarity_permille"])/10}%' if attr.get('rarity_permille') is not None else attr.get('rarity_category') or 'not returned'
                lines.append(f'{escape(attr["kind"])}={field(attr["filter_id"])} · {escape(attr["name"])} · rarity {escape(rarity)}')
            if not payload.get('attributes'):
                lines.append('Not returned on this page. Start a first-page query to request attributes.')
            key, _ = self.market_query(gift_id, options, offset)
            snapshots = self.store.snapshots(key)
            lines += ['', *[escape(l, 500) for l in comparison(snapshots)]]
            if len(snapshots) >= 2:
                lines.append('Compared snapshots: '+timestamp(snapshots[1][1])+' → '+timestamp(snapshots[0][1]))
            if payload.get('next_offset') and payload['next_offset'] != offset:
                selected = {k: options[k] for k in ['sort', 'currency', 'model', 'pattern', 'backdrop'] if k in options}
                token = self.store.token(scope, {'offset': payload['next_offset'], 'options': selected})
                lines += ['', f'Next API page: <code>/giftmarket {gift_id} next={token}</code>']
            await self.reply(event, 'Resale market', lines, self.source(result), page)
        elif name == 'giftvalue':
            result = await self.fetch('value:'+args[0], 'GetUniqueStarGiftValueInfoRequest', {'slug': args[0]}, CACHE_TTL, refresh, waiting)
            value = result[0]
            currency = escape(value.get('currency') or 'Currency not returned')
            lines = ['<b>Telegram estimate</b>', f'Estimated value: {field(value.get("value"))} {currency} minor units',
                     'Estimate basis: '+('Average of sold gifts of this type' if value.get('value_is_average') else 'Sale price of this gift'),
                     f'Type floor: {field(value.get("floor_price"))} {currency} minor units',
                     f'Type average sale price: {field(value.get("average_price"))} {currency} minor units',
                     '', '<b>Telegram-reported historical sales</b>',
                     f'Initial purchase: {field(value.get("initial_sale_stars"))} Stars · {timestamp(value.get("initial_sale_date"))}',
                     f'Initial converted price: {field(value.get("initial_sale_price"))} {currency} minor units']
            if value.get('last_sale_price') is not None and value.get('last_sale_date') is not None:
                lines += [f'Last completed resale reported by Telegram: {field(value["last_sale_price"])} {currency} minor units',
                          f'Last resale date: {timestamp(value["last_sale_date"])}',
                          'Last resale venue: '+('Fragment' if value.get('last_sale_on_fragment') else 'Not flagged as Fragment')]
            else:
                lines.append('Completed resale information: Not returned')
            lines += ['', '<b>Current listing counts</b>', f'Telegram: {field(value.get("listed_count"))} · Fragment: {field(value.get("fragment_listed_count"))}',
                      'Current asking prices: use /collectible SLUG or /giftmarket ID.',
                      'Minor units are preserved exactly as returned; no exchange rate or profit is calculated. This is not a full transaction history.']
            await self.reply(event, 'Gift valuation', lines, self.source(result), page)
        elif name == 'giftsearch':
            rows = [(item, fetched) for item, fetched in self.store.search() if self.matching(item, ' '.join(args), options)]
            lines = [f'Local matches: {len(rows)}', 'Search covers collected catalog and public collectible metadata only. Rarity matches any reported attribute; missing rarity does not match.']
            for item, fetched in rows:
                lines += ['']+gift_lines(item)+['Cached at: '+timestamp(fetched)]
            await self.reply(event, 'Local gift search', lines, 'Local cache only; no live API request. Rows may be stale.', page)
        elif name == 'giftwatch':
            if args[0] == 'list':
                lines = []
                for item in self.store.watches():
                    lines.append('Gift '+item['gift_id']+(' · last refresh failed: '+escape(item['last_error']) if item['last_error'] else ''))
                await self.reply(event, 'Local watchlist', lines or ['No watched gifts. Add one with /giftwatch ID.'], 'Local settings · 15-minute refresh while Potack runs.', page)
            elif args[0] == 'remove':
                self.store.unwatch(args[1])
                await self.reply(event, 'Watch removed', [f'Gift {args[1]} removed from the local watchlist.'])
            else:
                gift_id = args[0]
                # Validate with a successful read before adding a new persistent watch.
                result = await self.market(gift_id, status=waiting, refresh=refresh)
                if not self.store.watch(gift_id):
                    raise ResearchError('The conservative watchlist limit is 25 gifts. Remove one before adding another.')
                self.store.watch_error(gift_id, result[3])
                key, _ = self.market_query(gift_id)
                lines = [f'Watching gift {gift_id} locally. No trading actions are available.',
                         'Refresh: every 15 minutes while Potack runs; a first price-sorted page is sampled.']
                snapshots = self.store.snapshots(key)
                lines += [escape(l, 500) for l in comparison(snapshots)]
                if len(snapshots) >= 2:
                    lines += ['Compared snapshots: '+timestamp(snapshots[1][1])+' → '+timestamp(snapshots[0][1])]
                await self.reply(event, 'Gift watch', lines, self.source(result), page)
        elif name == 'giftstats':
            stats = self.store.stats()
            lines = [f'Cached gift records: {stats["catalog"]}', f'Cached API results: {stats["cache"]}',
                     f'Collected market snapshots: {stats["snapshots"]}', f'Watched gift types: {stats["watches"]}',
                     'Latest market sample: '+timestamp(stats['latest']), '', 'Recent snapshot comparisons:']
            for item in stats['recent']:
                snapshots = self.store.snapshots(item['query_key'])
                scope = json.loads(item['query_key'].removeprefix('market:'))
                selected = ', '.join(f'{k}={v}' for k, v in scope.items() if k not in {'id', 'offset'})
                lines += ['', 'Gift '+item['gift_id']+' · '+escape(selected, 300), *[escape(l, 500) for l in comparison(snapshots)]]
                if len(snapshots) >= 2:
                    lines.append(timestamp(snapshots[1][1])+' → '+timestamp(snapshots[0][1]))
            lines += ['', 'Available read methods:']
            for method, supported in self.api.supported().items():
                lines.append(escape(method.removesuffix('Request'))+': '+('Supported' if supported else 'Unavailable'))
            await self.reply(event, 'Local research statistics', lines, 'Local cache and actual snapshots; no invented market history.', page)
        elif name == 'giftexport':
            form, dataset = args[:2]
            if dataset == 'catalog':
                rows = [{**item, 'fetched_at': fetched, 'source': 'local API cache; may be stale'} for item, fetched in self.store.search()]
            else:
                key, _ = self.market_query(args[2])
                snapshots = self.store.snapshots(key, limit=1000)
                rows = [{'fetched_at': fetched, 'gift_id': args[2], 'source': 'collected price-sorted first API page',
                         'listing_count': payload.get('count'), 'sampled_listings': payload['gifts']} for payload, fetched in snapshots]
            if not rows:
                raise ResearchError('No collected data exists for this export. Research the catalog or market first.')
            path = self.store.export(form, dataset, rows)
            await self.reply(event, 'Local export complete', [f'Rows: {len(rows)}', '<code>'+escape(str(path.relative_to(self.directory.parent)), 250)+'</code>',
                                                             'The file stays on this computer. No file was uploaded to Telegram.'])

    async def refresh_watches(self):
        for watch in self.store.watches():
            gift_id = int(watch['gift_id'])
            key, _ = self.market_query(gift_id)
            cached = self.store.get(key)
            if cached and self.clock()-cached[1] < WATCH_INTERVAL:
                continue
            try:
                result = await self.market(gift_id, refresh=True)
                self.store.watch_error(gift_id, result[3])
            except ResearchError as error:
                self.store.watch_error(gift_id, str(error))
            except Exception:
                self.store.watch_error(gift_id, 'Local research refresh failed.')

    async def watch_loop(self):
        while True:
            # No unsolicited Telegram messages. Sleep first to avoid login bursts.
            await asyncio.sleep(WATCH_INTERVAL)
            try:
                await self.refresh_watches()
            except Exception:
                pass  # Safe to retry next cycle; never dump account/exception data.
