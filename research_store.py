"""Private local SQLite cache, genuine snapshots, watchlist, and local exports."""
import csv
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time


class ResearchStore:
    def __init__(self, directory, clock=time.time):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.clock = clock
        path = self.directory / 'research.sqlite3'
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(descriptor)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY, fetched REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS catalog (
                key TEXT PRIMARY KEY, fetched REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY, query_key TEXT NOT NULL, gift_id TEXT NOT NULL,
                fetched REAL NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS snapshot_query ON snapshots(query_key, id DESC);
            CREATE TABLE IF NOT EXISTS watches (
                gift_id TEXT PRIMARY KEY, added REAL NOT NULL, last_error TEXT);
            CREATE TABLE IF NOT EXISTS tokens (
                token TEXT PRIMARY KEY, scope TEXT NOT NULL, fetched REAL NOT NULL,
                payload TEXT NOT NULL);
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, key):
        row = self.db.execute('SELECT fetched,payload FROM cache WHERE key=?', (key,)).fetchone()
        return (json.loads(row['payload']), row['fetched']) if row else None

    def put(self, key, payload, fetched=None):
        fetched = self.clock() if fetched is None else fetched
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (key, fetched, json.dumps(payload)))
            # Bound page/token cache without discarding research snapshot history.
            self.db.execute('DELETE FROM cache WHERE key NOT IN (SELECT key FROM cache ORDER BY fetched DESC LIMIT 2000)')
        return fetched

    def index_gifts(self, gifts, fetched, replace_catalog=False):
        with self.db:
            if replace_catalog:
                self.db.execute("DELETE FROM catalog WHERE key LIKE 'catalog:%'")
            for item in gifts:
                key = item['kind'] + ':' + str(item.get('slug') or item['id'])
                self.db.execute('INSERT OR REPLACE INTO catalog VALUES (?,?,?)', (key, fetched, json.dumps(item)))

    def search(self):
        return [(json.loads(r['payload']), r['fetched']) for r in self.db.execute('SELECT * FROM catalog ORDER BY key')]

    def snapshot(self, query_key, gift_id, payload, fetched):
        with self.db:
            self.db.execute('INSERT INTO snapshots(query_key,gift_id,fetched,payload) VALUES (?,?,?,?)',
                            (query_key, str(gift_id), fetched, json.dumps(payload)))

    def snapshots(self, query_key, limit=2):
        return [(json.loads(r['payload']), r['fetched']) for r in self.db.execute(
            'SELECT * FROM snapshots WHERE query_key=? ORDER BY id DESC LIMIT ?', (query_key, limit))]

    def watch(self, gift_id):
        key = str(gift_id)
        if not self.db.execute('SELECT 1 FROM watches WHERE gift_id=?', (key,)).fetchone() and len(self.watches()) >= 25:
            return False
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO watches(gift_id,added) VALUES (?,?)', (key, self.clock()))
        return True

    def unwatch(self, gift_id):
        with self.db:
            self.db.execute('DELETE FROM watches WHERE gift_id=?', (str(gift_id),))

    def watch_error(self, gift_id, message):
        with self.db:
            self.db.execute('UPDATE watches SET last_error=? WHERE gift_id=?', (message, str(gift_id)))

    def watches(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM watches ORDER BY added')]

    def token(self, scope, payload):
        key = 'p' + secrets.token_hex(6)
        with self.db:
            self.db.execute('DELETE FROM tokens WHERE fetched<?', (self.clock()-3600,))
            self.db.execute('INSERT INTO tokens VALUES (?,?,?,?)', (key, scope, self.clock(), json.dumps(payload)))
            self.db.execute('DELETE FROM tokens WHERE token NOT IN (SELECT token FROM tokens ORDER BY fetched DESC LIMIT 1000)')
        return key

    def resolve(self, token, scope):
        row = self.db.execute('SELECT * FROM tokens WHERE token=? AND scope=? AND fetched>?',
                              (token, scope, self.clock()-3600)).fetchone()
        return json.loads(row['payload']) if row else None

    def stats(self):
        result = {}
        for table in ['catalog', 'cache', 'snapshots', 'watches']:
            result[table] = self.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        result['latest'] = self.db.execute('SELECT max(fetched) FROM snapshots').fetchone()[0]
        result['recent'] = [dict(r) for r in self.db.execute('SELECT query_key,gift_id FROM snapshots GROUP BY query_key,gift_id ORDER BY max(id) DESC LIMIT 5')]
        return result

    def export(self, form, dataset, rows):
        destination = self.directory / 'exports'
        destination.mkdir(exist_ok=True, mode=0o700)
        os.chmod(destination, 0o700)
        suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(3)
        filename = destination / f'{dataset}-{suffix}.{form}'
        descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            if form == 'json':
                json.dump({'dataset': dataset, 'exported_at': self.clock(), 'rows': rows}, stream, ensure_ascii=False, indent=2)
            else:
                fields = sorted({field for row in rows for field in row}) or ['dataset']
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    safe = {}
                    for key, value in row.items():
                        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value if value is not None else '')
                        # Protect spreadsheets from formulas originating in API titles.
                        safe[key] = "'"+text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text
                    writer.writerow(safe)
        return filename


def observed_prices(payload):
    result = {}
    for item in payload.get('gifts', []):
        for price in item.get('asking_prices', []):
            currency = price['currency']
            value = Decimal(price['amount'])
            result[currency] = min(result.get(currency, value), value)
    return result


def comparison(snapshots):
    if len(snapshots) < 2:
        return ['No comparison yet. Two collected snapshots are required.']
    (new, new_at), (old, old_at) = snapshots[:2]
    lines = [f'Observed listing count: {old.get("count", "not returned")} → {new.get("count", "not returned")}']
    before, after = observed_prices(old), observed_prices(new)
    for currency in sorted(set(before) | set(after)):
        if currency not in before or currency not in after:
            lines.append(f'{currency}: no comparable asking price in both sampled pages.')
        else:
            delta = after[currency] - before[currency]
            lines.append(f'Lowest observed {currency} ask: {before[currency]} → {after[currency]} (change {delta:+f})')
    lines.append('These are sampled asking prices, not completed sales or profit.')
    return lines
