"""Narrow read-only MTProto boundary. Never serialize raw account/API responses."""
import asyncio
from datetime import datetime
from decimal import Decimal
import time

from telethon.errors import FloodWaitError, RPCError, ServerError, TimedOutError
from telethon.tl import functions, types

# Explicit names are the entire API surface. No user-supplied method dispatch.
READ_METHODS = (
    'GetStarGiftsRequest', 'GetSavedStarGiftsRequest', 'GetUniqueStarGiftRequest',
    'GetResaleStarGiftsRequest', 'GetUniqueStarGiftValueInfoRequest', 'GetStarsStatusRequest',
)


class ResearchError(Exception):
    """A safe, user-facing message with no exception text or authentication data."""


class ReadOnlyAPI:
    def __init__(self, client, interval=2.5, sleep=asyncio.sleep, clock=time.monotonic):
        self.client = client
        self.interval = interval
        self.sleep = sleep
        self.clock = clock
        self.lock = asyncio.Lock()
        self.not_before = 0.0
        self.allowed = {name: getattr(functions.payments, name, None) for name in READ_METHODS}
        self.owner_id = None

    async def owner(self):
        if self.owner_id is None:
            try:
                me = await self.client.get_me()
            except Exception:
                raise ResearchError('Unable to verify your authenticated account. Try again later.') from None
            if me is None or getattr(me, 'bot', False):
                raise ResearchError('This feature requires your authenticated Telegram user account.')
            self.owner_id = me.id
        return self.owner_id

    def supported(self):
        return {name: cls is not None for name, cls in self.allowed.items()}

    async def read(self, name, status=None, **kwargs):
        cls = self.allowed.get(name)
        if name not in READ_METHODS:
            raise ResearchError('This operation is blocked by the read-only research policy.')
        if cls is None:
            raise ResearchError('This read method is unavailable in the installed Telethon version.')
        if name in {'GetSavedStarGiftsRequest', 'GetStarsStatusRequest'}:
            # The caller cannot substitute another user's peer.
            kwargs['peer'] = types.InputPeerSelf()
        try:
            request = cls(**kwargs)
        except TypeError:
            raise ResearchError('The installed Telethon schema does not support these read parameters.') from None
        # Exact type check also rejects request wrappers and subclasses.
        if type(request) not in {c for c in self.allowed.values() if c is not None}:
            raise ResearchError('This operation is blocked by the read-only research policy.')
        async with self.lock:
            for attempt in range(3):
                delay = max(0, self.not_before - self.clock())
                if delay:
                    await self.sleep(delay)
                self.not_before = self.clock() + self.interval
                try:
                    # Per-request threshold avoids Telethon silently sleeping in this module.
                    return await self.client(request, flood_sleep_threshold=0)
                except FloodWaitError as error:
                    self.not_before = max(self.not_before, self.clock() + error.seconds)
                    if status:
                        await status(f'⏳ Telegram asked Potack to wait {error.seconds} seconds.\nResearch will resume safely.')
                    if attempt == 2:
                        raise ResearchError('Telegram is still rate limiting research. Try again after the wait.') from None
                except (OSError, asyncio.TimeoutError, ServerError, TimedOutError):
                    self.not_before = max(self.not_before, self.clock() + 2 ** (attempt + 1))
                    if attempt == 2:
                        raise ResearchError('Telegram could not be reached. No verified live result is available.') from None
                except RPCError as error:
                    code = getattr(error, 'code', 0)
                    if getattr(error, 'message', None) in {'METHOD_INVALID', 'INPUT_METHOD_INVALID'}:
                        message = 'This read method is unavailable on the current Telegram API connection.'
                    elif code == 400:
                        message = 'Telegram rejected this ID, slug, filter, or page. Check the input.'
                    elif code in {401, 403}:
                        message = 'Telegram does not permit this read for the authenticated account.'
                    else:
                        message = 'Telegram could not provide this research result.'
                    raise ResearchError(message) from None
                except Exception:
                    raise ResearchError('The current API response could not be read safely. No result was invented.') from None


def decimal_text(number):
    return format(number, 'f').rstrip('0').rstrip('.') if '.' in format(number, 'f') else format(number, 'f')


def amount(value):
    if isinstance(value, types.StarsAmount):
        number = Decimal(value.amount) + Decimal(value.nanos) / Decimal(10**9)
        return {'currency': 'Stars', 'amount': decimal_text(number)}
    if isinstance(value, types.StarsTonAmount):
        return {'currency': 'TON', 'amount': decimal_text(Decimal(value.amount) / Decimal(10**9))}
    return None


def scalar(value):
    if isinstance(value, datetime):
        return value.timestamp()
    return value if isinstance(value, (str, int, bool, float)) or value is None else None


def attribute(value):
    # OriginalDetails contains other people's IDs/messages: deliberately excluded.
    kinds = {types.StarGiftAttributeModel: 'model', types.StarGiftAttributePattern: 'pattern',
             types.StarGiftAttributeBackdrop: 'backdrop'}
    kind = kinds.get(type(value))
    if not kind:
        return None
    rarity = getattr(value, 'rarity', None)
    categories = {'StarGiftAttributeRarityUncommon': 'uncommon', 'StarGiftAttributeRarityRare': 'rare',
                  'StarGiftAttributeRarityEpic': 'epic', 'StarGiftAttributeRarityLegendary': 'legendary'}
    result = {'kind': kind, 'name': value.name, 'rarity_permille': getattr(rarity, 'permille', None),
              'rarity_category': categories.get(type(rarity).__name__)}
    if kind == 'backdrop':
        result['filter_id'] = value.backdrop_id
        for name in ['center_color', 'edge_color', 'pattern_color', 'text_color']:
            result[name] = getattr(value, name, None)
    else:
        result['filter_id'] = getattr(value.document, 'id', None)
    if kind == 'model':
        result['crafted'] = bool(getattr(value, 'crafted', False))
    return result


def gift(value):
    """Retain documented research fields only; exclude owners, wallets, and messages."""
    if isinstance(value, types.StarGiftUnique):
        names = ['id', 'gift_id', 'title', 'slug', 'num', 'availability_issued', 'availability_total',
                 'require_premium', 'resale_ton_only', 'burned', 'crafted', 'value_amount',
                 'value_currency', 'value_usd_amount', 'craft_chance_permille']
        result = {'kind': 'collectible', **{name: scalar(getattr(value, name, None)) for name in names}}
        result['attributes'] = [item for item in (attribute(a) for a in value.attributes) if item]
        result['asking_prices'] = [item for item in (amount(a) for a in (value.resell_amount or [])) if item]
        return result
    if isinstance(value, types.StarGift):
        names = ['id', 'title', 'stars', 'limited', 'sold_out', 'require_premium', 'limited_per_user',
                 'availability_remains', 'availability_total', 'availability_resale', 'upgrade_stars',
                 'upgrade_variants', 'resell_min_stars', 'first_sale_date', 'last_sale_date',
                 'locked_until_date', 'auction', 'auction_slug', 'gifts_per_round', 'auction_start_date']
        return {'kind': 'catalog', **{name: scalar(getattr(value, name, None)) for name in names}}
    raise ResearchError('Telegram returned an unsupported gift type; no metadata was invented.')


def normalize(name, response):
    if name == 'GetStarGiftsRequest':
        if isinstance(response, types.payments.StarGiftsNotModified):
            raise ResearchError('Telegram returned an unchanged catalog without a fresh payload. Use the existing cache.')
        return {'gifts': [gift(g) for g in response.gifts], 'hash': response.hash}
    if name == 'GetUniqueStarGiftRequest':
        return gift(response.gift)
    if name == 'GetSavedStarGiftsRequest':
        # No raw SavedStarGift messages, sender IDs, prepaid hashes, or user vectors.
        items = []
        for saved in response.gifts:
            item = gift(saved.gift)
            item['received_at'] = scalar(saved.date)
            item['can_upgrade'] = bool(saved.can_upgrade)
            item['profile_saved'] = not bool(saved.unsaved)
            items.append(item)
        return {'gifts': items, 'count': response.count, 'next_offset': response.next_offset}
    if name == 'GetResaleStarGiftsRequest':
        return {'gifts': [gift(g) for g in response.gifts], 'count': response.count,
                'next_offset': response.next_offset,
                'attributes': [item for item in (attribute(a) for a in (response.attributes or [])) if item]}
    if name == 'GetUniqueStarGiftValueInfoRequest':
        names = ['currency', 'value', 'value_is_average', 'initial_sale_date', 'initial_sale_stars',
                 'initial_sale_price', 'last_sale_date', 'last_sale_price', 'last_sale_on_fragment',
                 'floor_price', 'average_price', 'listed_count', 'fragment_listed_count']
        return {name: scalar(getattr(response, name, None)) for name in names}
    if name == 'GetStarsStatusRequest':
        balance = amount(response.balance)
        if not balance or balance['currency'] != 'Stars':
            raise ResearchError('Telegram did not return a readable Stars balance.')
        return {'balance': balance}
    raise ResearchError('Unsupported read response.')
