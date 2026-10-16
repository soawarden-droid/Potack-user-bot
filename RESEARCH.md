# POTACK

Telegram Gifts Research Engine

Created by xa

This extends the existing `main.py` userbot. Python 3.12, Telethon 1.45.0 (installed MTProto layer 229), and requests 2.34.2 remain in use. No packages needed upgrading. The existing account session and owner-only private configuration are unchanged. No new bot account or bot token is needed.

## Start on Ubuntu

The upgrade is active using the existing account session. Implementation tests used a mocked client and made no live research requests. Stop the current instance before starting another copy; never run two copies using the same session.

From the project directory:

```bash
cd /home/aza-apricity/telegramuserbot
./venv/bin/python main.py
```

Alternatively, in VS Code's terminal:

```bash
cd /home/aza-apricity/telegramuserbot
source venv/bin/activate
python main.py
```

The research worker starts after the existing Telethon login completes and stops when the userbot disconnects. Existing login behavior is preserved; Potack does not automatically connect wallets or authenticate with third parties.

## Simple architecture

`main.py` still runs your userbot. It sends research commands to `research.py`. `research_api.py` asks Telegram for information through six permitted read methods. `research_store.py` saves selected information and real market samples in a private SQLite file. `research_format.py` turns results into readable Telegram messages. Old commands still use their original handlers.

## Commands

Send these from your own account. Commands do not take a dot prefix or a bot username suffix. IDs must be Telegram gift type IDs; collectible slugs look like `Rose-123`.

| Command | Result |
| --- | --- |
| `/gifts` | Current gift catalog, gift IDs, Stars prices, availability, upgrade prices |
| `/giftinfo ID` | Detailed metadata for a type in the returned catalog |
| `/collectible SLUG` | Unique collectible title, number, base type, supply, model, backdrop, pattern, reported rarity, asks |
| `/mygifts` | Gifts owned or received by your account, including gifts not saved to your profile; excludes hosted gifts |
| `/giftmarket ID` | One resale listing page, current asks, reported query count, available attribute filters |
| `/giftvalue SLUG` | Telegram estimates, reported initial purchase and last completed resale, type price statistics, listing counts |
| `/stars` | Your own Stars balance, with exact fractional precision |
| `/giftwatch ID` | Add a local watch and show actual snapshot comparisons |
| `/giftwatch refresh ID` | Request a new sample for that watched gift |
| `/giftwatch list` | Watches and refresh failures |
| `/giftwatch remove ID` | Remove only the local watch entry |
| `/giftstats` | Cache counts, stored snapshot counts, recent comparisons, supported read methods |
| `/giftsearch "Rose"` | Search already collected local catalog and public collectible metadata |
| `/giftexport json catalog` | Export local cached metadata as JSON |
| `/giftexport csv catalog` | Export local cached metadata as CSV |
| `/giftexport json market ID` | Export up to 1000 stored first-page price-sorted samples for that type |
| `/giftexport csv market ID` | Same samples in CSV; nested listing records use JSON cells |
| `/gifthelp` | Guide, filters, pagination |

All research commands verify the outgoing message's sender ID against the authenticated account ID. Incoming messages and messages sent as a channel are ignored. `/stars` and `/mygifts` work only in Saved Messages, so balances and owned-gift details cannot accidentally be posted in a group. Exports remain local; nothing is uploaded.

### Pagination and filters

Catalog, detail, help, search, and stats messages use `page=N`. The page count is displayed in the footer. Repeat the same command and options with a different page, for example `/gifts page=2`.

Owned-gift and market reads fetch at most eight results at a time. Follow the returned command containing `next=p…` for the next API page. Tokens expire after one hour, are scoped to the account or gift type, and do not reveal Telegram's opaque API offsets. A long API page can additionally have multiple display pages: retain the same `next` token and add `page=2` to read another display page of that API result. Market page tokens retain their original filters; changing filters requires a new first-page query.

Examples:

```text
/gifts search="Rose" available=true upgradable=true
/giftmarket ID sort=price currency=stars model=MODEL_ID backdrop=BACKDROP_ID
/giftmarket ID sort=number
/giftsearch "Rose" model="Gold" rarity<=1
/giftsearch "Rose" rarity=rare
/giftsearch "Rose" backdrop="Blue" pattern="Stars" page=2
/collectible Rose-123 refresh
```

Replace ID placeholders with IDs returned by Telegram. Market `model` and `pattern` filters use document IDs; `backdrop` uses its signed 32-bit backdrop ID. `sort` is `price`, `number`, or `recent`; `currency` is `stars` or `all`. Sort by price and number are mutually exclusive. Attribute availability is requested on first pages and may be absent on later pages.

Catalog `available=true` means “not marked sold out”; it does not guarantee buying eligibility. `upgradable=true` selects types with a returned upgrade price, including zero. These are metadata filters, never buying actions.

Local rarity filtering uses only Telegram-reported numeric rarity or qualitative categories. `rarity<=1` means an attribute with reported rarity of at most 1%; `rarity=rare` matches that exact category. Rarity matches any reported attribute of a collectible. Missing rarity never passes a rarity filter. No combined rarity score, guessed probability, or profit estimate is calculated.

## Data quality and privacy

Responses say **Verified live API result**, **Cached API result**, or **STALE cached result; live refresh failed** and retain the original fetch time. Displayed timestamps use Asia/Kabul. A stale result is not counted as a new sample. Missing metadata says “Not returned,” rather than inventing zero values. The local search may be incomplete: it contains only metadata already fetched through catalog, collectible, or resale reads.

Catalog, collectible, and valuation caches normally last ten minutes. Resale and owned-gift page caches last one minute. Add `refresh` to supported reads to request live data; rate limits still apply. A fresh server response becomes a new snapshot even when prices did not change.

Each watch reads one price-sorted first page every 15 minutes while Potack runs, with a maximum of 25 gift types. Recent samples suppress unnecessary background reads. No notifications or chat messages are sent by the background worker. `/giftwatch` and `/giftstats` show collected changes and stored refresh errors.

Market comparisons require two actual samples with the same gift, filter, sort, and offset. They compare the returned listing count and the lowest **observed** asking price in each currency within the sampled page. They are not a full-market floor, a completed sale, or investment performance. Stars and TON remain separate; no exchange rates are guessed.

`/giftvalue` keeps Telegram's fiat values in their documented smallest currency units, labeled explicitly. It separates value estimates, Telegram-reported historical purchases/resales, and current listing counts. It only reports a completed resale when the API returns both a sale price and date. This is not a complete sales ledger.

The cache is `research_data/research.sqlite3`; exports are under `research_data/exports/`. The directory is owner-only (0700), and database/export files are 0600. These are excluded from Git and code backups. Raw response user/chat vectors, phone numbers, original gift messages, sender/recipient IDs, wallet addresses, and prepaid hashes are never cached or exported. Your Stars balance is not persisted. Own-gift cache pages use an account-scoped hash key. Errors never include raw exception text or request data. CSV text is guarded against spreadsheet formula injection.

## Official API research and method boundary

This is a Telethon user-account implementation. Telegram's [Bot Payments Stars guide](https://core.telegram.org/bots/payments-stars) describes bot payment flows and is not used to implement these research reads. The general [Gifts guide](https://core.telegram.org/api/gifts) and [Stars guide](https://core.telegram.org/api/stars) were reviewed alongside the concrete method and constructor schemas.

Only these six request classes can cross the research API boundary:

| Telethon class | Official method |
| --- | --- |
| `GetStarGiftsRequest` | [payments.getStarGifts](https://core.telegram.org/method/payments.getStarGifts) |
| `GetSavedStarGiftsRequest` | [payments.getSavedStarGifts](https://core.telegram.org/method/payments.getSavedStarGifts) |
| `GetUniqueStarGiftRequest` | [payments.getUniqueStarGift](https://core.telegram.org/method/payments.getUniqueStarGift) |
| `GetResaleStarGiftsRequest` | [payments.getResaleStarGifts](https://core.telegram.org/method/payments.getResaleStarGifts) |
| `GetUniqueStarGiftValueInfoRequest` | [payments.getUniqueStarGiftValueInfo](https://core.telegram.org/method/payments.getUniqueStarGiftValueInfo) |
| `GetStarsStatusRequest` | [payments.getStarsStatus](https://core.telegram.org/method/payments.getStarsStatus) |

Each is present in the installed Telethon version. Availability is checked at runtime, and schema incompatibilities produce a limitation message. Own-data peer arguments are forcibly set to `InputPeerSelf`. No arbitrary method names, request wrappers, payment forms, invoices, transfers, purchases, resale updates, upgrades, conversion, crafting, burning, or withdrawal requests are accepted.

Valuation labels follow [uniqueStarGiftValueInfo](https://core.telegram.org/constructor/payments.uniqueStarGiftValueInfo). Collectible data follows [starGiftUnique](https://core.telegram.org/constructor/starGiftUnique). Numeric Stars and TON amounts follow [starsAmount](https://core.telegram.org/constructor/starsAmount) and [starsTonAmount](https://core.telegram.org/constructor/starsTonAmount).

Reads are serialized with at least 2.5 seconds between attempts. FloodWait delays pause future reads and are shown during foreground commands. Transient network/server failures get bounded exponential backoff; three failed attempts produce a friendly error or clearly marked stale result. Cached/local-only commands make no research API calls. One foreground research command runs per chat at a time. Research responses edit the command message, with Telegram HTML escaping and pagination below the message size limit.

“Read-only” applies to the new Gifts/Stars engine. Existing cleanup still deletes messages when you explicitly invoke Qalawa, and existing notes/formatting commands still make their intended changes. No pre-existing functions have been removed.

## Validation and recovery

Run:

```bash
cd /home/aza-apricity/telegramuserbot
./venv/bin/python -m py_compile main.py cleanup.py research.py research_api.py research_store.py research_format.py
./venv/bin/python -m unittest discover -s tests -v
```

Tests use actual Telethon schema objects and a mocked client. They do not log in, touch real sessions, or make Telegram requests. They cover every research command, strict parsing, exact currency handling, account authorization, owned-data privacy, page/filter scope, message limits, stale caches, missing sale data, FloodWait/retries, watch changes, exports, and blocking every payments request outside the six-method allowlist. Existing cleanup tests and offline integration checks exercise handler registration, persistent formatting, notes, help, and ping.

A recoverable, secret-free checkpoint exists under `backups/before-gifts-20261013-174858Z/`, including the prior source files, tests, dependency list, restore instructions, and a SHA-256 manifest. Configuration, notes, sessions, logs, databases, and exports are intentionally excluded. Existing Downloads variants remain untouched.
