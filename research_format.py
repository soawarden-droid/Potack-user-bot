"""Telegram-safe presentation; values are escaped before entering HTML."""
from datetime import datetime
from decimal import Decimal
import html
from zoneinfo import ZoneInfo

from cleanup import SIGNATURE

TIMEZONE = ZoneInfo('Asia/Kabul')
MAX_UNITS = 3900
HEADER = '<b>POTACK</b>\nTelegram Gifts Research Engine'


def escape(value, limit=180):
    text = str(value)
    if len(text) > limit:
        text = text[:limit-1] + '…'
    return html.escape(text, quote=True)


def timestamp(value):
    if value is None:
        return 'Not returned'
    return datetime.fromtimestamp(value, TIMEZONE).strftime('%Y-%m-%d %H:%M:%S Kabul')


def field(value):
    if value is None:
        return 'Not returned'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return escape(value)


def units(text):
    return len(text.encode('utf-16-le')) // 2


def render(title, lines, source=None, page=1):
    """Paginate complete HTML lines without splitting entities or tags."""
    prefix = HEADER + '\n\n<b>' + escape(title) + '</b>\n'
    if source:
        prefix += escape(source, 420) + '\n\n'
    footer = '\n\n' + SIGNATURE
    pages, current = [], []
    for line in lines or ['No results.']:
        if units(prefix + '\n'.join(current + [line]) + footer) > MAX_UNITS - 70 and current:
            pages.append(current)
            current = []
        if units(prefix + line + footer) > MAX_UNITS - 70:
            # Callers compose bounded lines; fail safely if this invariant breaks.
            line = escape(html.unescape(line), 800)
        current.append(line)
    pages.append(current)
    if page < 1 or page > len(pages):
        raise ValueError(f'Page must be between 1 and {len(pages)}.')
    result = prefix + '\n'.join(pages[page-1]) + f'\n\nPage {page} / {len(pages)}' + footer
    return result, len(pages)


def attribute_lines(item):
    lines = []
    for attr in item.get('attributes', []):
        rarity = attr.get('rarity_category')
        permille = attr.get('rarity_permille')
        if permille is not None:
            rarity = f'{Decimal(permille)/10}%'
        lines.append(f'{escape(attr["kind"].title())}: {escape(attr["name"])} · rarity {escape(rarity or "not returned")}')
    return lines


def price_text(prices):
    return ' / '.join(escape(p['amount'])+' '+escape(p['currency']) for p in prices) or 'Not returned'


def gift_lines(item, full=False):
    label = item.get('title') or ('Gift ' + str(item['id']))
    lines = [f'<b>{escape(label)}</b> · ID <code>{item["id"]}</code>']
    if item['kind'] == 'catalog':
        lines.append(f'Catalog price: {field(item.get("stars"))} Stars')
        lines.append('Availability: ' + ('Sold out' if item.get('sold_out') else 'Not marked sold out'))
        if item.get('limited'):
            lines.append(f'Remaining / total: {field(item.get("availability_remains"))} / {field(item.get("availability_total"))}')
        lines.append(f'Upgrade price: {field(item.get("upgrade_stars"))}' + (' Stars' if item.get('upgrade_stars') is not None else ''))
        if full:
            for name, label in [('upgrade_variants', 'Upgrade variants'), ('availability_resale', 'Resale availability'),
                                ('resell_min_stars', 'Telegram minimum resale Stars'), ('require_premium', 'Requires Premium'),
                                ('limited_per_user', 'Limited per user'), ('auction', 'Auction gift')]:
                lines.append(f'{label}: {field(item.get(name))}')
            for name, label in [('first_sale_date', 'First catalog sale'), ('last_sale_date', 'Last catalog sale'),
                                ('locked_until_date', 'Locked until'), ('auction_start_date', 'Auction starts')]:
                lines.append(f'{label}: {timestamp(item.get(name))}')
    else:
        lines.append(f'Slug: <code>{escape(item["slug"])}</code> · Number: {field(item.get("num"))}')
        lines.append(f'Base gift ID: <code>{item["gift_id"]}</code>')
        lines.append(f'Issued / maximum upgrade supply: {field(item.get("availability_issued"))} / {field(item.get("availability_total"))}')
        lines.extend(attribute_lines(item))
        lines.append('Current asking price: ' + price_text(item.get('asking_prices', [])))
        if full:
            lines.append(f'Burned: {field(item.get("burned"))} · Crafted: {field(item.get("crafted"))}')
            for key, currency_key, label in [('value_amount', 'value_currency', 'Telegram value')]:
                if item.get(key) is not None:
                    lines.append(f'{label}: {field(item[key])} {field(item.get(currency_key))} minor units')
            if item.get('value_usd_amount') is not None:
                lines.append(f'Telegram USD value: {field(item["value_usd_amount"])} USD minor units')
    if 'received_at' in item:
        lines.append(f'Received: {timestamp(item["received_at"])} · Saved to profile: {field(item.get("profile_saved"))}')
    return lines
