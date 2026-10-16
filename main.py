
from telethon import TelegramClient, events
import asyncio
import html
import json
import os
import requests
from cleanup import Cleanup, branded, parse_command
from research import Research, is_research_command


# ============================================================
# POTACK
# Personal Telegram Toolkit
# Created by xa
# ============================================================


# =========================
# CONFIG
# =========================

from pathlib import Path
BASE_DIR = Path(os.environ.get("POTACK_HOME", Path(__file__).resolve().parent))
with (BASE_DIR / "potack_private.json").open(encoding="utf-8") as private_file:
    PRIVATE_CONFIG = json.load(private_file)

api_id = PRIVATE_CONFIG['api_id']

# Credentials are loaded from the owner-only private configuration.
api_hash = PRIVATE_CONFIG['api_hash']

# OpenRouter credentials use the same private configuration.
OPENROUTER_API_KEY = PRIVATE_CONFIG['OPENROUTER_API_KEY']

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
AI_MODEL = "openrouter/free"

SEARXNG_URL = PRIVATE_CONFIG.get("SEARXNG_URL", "http://127.0.0.1:8080").rstrip("/")

DATA_FILE = str(BASE_DIR / "potack_data.json")


# =========================
# CLIENT
# =========================

client = TelegramClient(str(BASE_DIR / PRIVATE_CONFIG['session_name']), api_id, api_hash, flood_sleep_threshold=0,
                        receive_updates=not PRIVATE_CONFIG.get("android_paused", False))
client.parse_mode = 'html'


# =========================
# STORAGE
# =========================

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "notes": {},
            "format_modes": {}
        }

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            data = {}

        data.setdefault("notes", {})
        data.setdefault("format_modes", {})

        return data

    except Exception:
        return {
            "notes": {},
            "format_modes": {}
        }


def save_data():
    # Replace atomically so an interrupted settings write preserves existing notes.
    target = Path(DATA_FILE)
    temporary = target.with_suffix(".json.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


data = load_data()


# =========================
# FORMAT STATE
# =========================

DEFAULT_FORMAT = {
    "bold": False,
    "italic": False,
    "underline": False,
    "strike": False,
    "code": False
}


def get_format(chat_id):
    key = str(chat_id)

    if key not in data["format_modes"]:
        data["format_modes"][key] = DEFAULT_FORMAT.copy()
        save_data()

    current = data["format_modes"][key]

    for name in DEFAULT_FORMAT:
        current.setdefault(name, False)

    return current


def format_enabled(chat_id):
    modes = get_format(chat_id)

    return any(modes.values())


def apply_format(text, modes):
    if not text:
        return text

    escaped = html.escape(text)

    if modes.get("code"):
        escaped = f"<code>{escaped}</code>"

    if modes.get("strike"):
        escaped = f"<s>{escaped}</s>"

    if modes.get("underline"):
        escaped = f"<u>{escaped}</u>"

    if modes.get("italic"):
        escaped = f"<i>{escaped}</i>"

    if modes.get("bold"):
        escaped = f"<b>{escaped}</b>"

    return escaped


def format_status_text(chat_id):
    modes = get_format(chat_id)

    active = []

    names = {
        "bold": "BOLD",
        "italic": "ITALIC",
        "underline": "UNDERLINE",
        "strike": "STRIKE",
        "code": "CODE"
    }

    for key, label in names.items():
        if modes.get(key):
            active.append(label)

    if not active:
        return "OFF"

    return " + ".join(active)


# =========================
# OPENROUTER
# =========================

def openrouter_request(
    messages,
    temperature=0.3,
    max_tokens=700
):
    if (
        not OPENROUTER_API_KEY
        or OPENROUTER_API_KEY.startswith("PASTE_")
    ):
        raise RuntimeError(
            "OpenRouter API key is not configured."
        )

    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization":
                f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type":
                "application/json",
            "HTTP-Referer":
                "https://potack.local",
            "X-Title":
                "Potack"
        },
        json={
            "model": AI_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        },
        timeout=60
    )

    if response.status_code == 401:
        raise RuntimeError(
            "OpenRouter rejected the API key (401). "
            "Check the private configuration."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "OpenRouter rate limit reached."
        )

    if not response.ok:
        try:
            error = response.json()
            message = error.get("error", {}).get(
                "message",
                response.text
            )
        except Exception:
            message = response.text

        raise RuntimeError(
            f"OpenRouter error {response.status_code}: "
            f"{message}"
        )

    result = response.json()

    choices = result.get("choices", [])

    if not choices:
        raise RuntimeError(
            "OpenRouter returned no answer."
        )

    message = choices[0].get("message", {})

    answer = message.get("content", "")

    if not answer:
        raise RuntimeError(
            "OpenRouter returned an empty answer."
        )

    return str(answer).strip()


def ask_ai(question):
    messages = [
        {
            "role": "system",
            "content": (
                "You are Potack AI. "
                "Answer clearly and naturally. "
                "Use simple language. "
                "Do not write unnecessary introductions. "
                "Keep answers concise unless the user asks "
                "for detail."
            )
        },
        {
            "role": "user",
            "content": question
        }
    ]

    return openrouter_request(
        messages,
        temperature=0.3,
        max_tokens=700
    )


def summarize_search(query, results):
    source_text = []

    for index, result in enumerate(results, 1):
        source_text.append(
            f"SOURCE {index}\n"
            f"Title: {result['title']}\n"
            f"URL: {result['url']}\n"
            f"Snippet: {result['content']}\n"
        )

    research = "\n".join(source_text)

    prompt = f"""
You are the web-answer engine inside Potack.

User question:
{query}

Search results:
{research}

Give the user a SHORT, useful answer.

Rules:
- Start with the direct answer.
- Use simple human language.
- Do not dump search results.
- Do not mention Wikipedia unless it is genuinely important.
- Do not invent facts.
- Prefer information supported by the supplied sources.
- Maximum about 5 short paragraphs.
- At the end write:

Sources:
• Source name — URL
• Source name — URL

Use only the supplied URLs.
"""

    return openrouter_request(
        [
            {
                "role": "system",
                "content": (
                    "You summarize web search results "
                    "accurately and briefly."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2,
        max_tokens=900
    )


# =========================
# SEARXNG
# =========================

def searxng_search(query, max_results=6):
    if not SEARXNG_URL:
        raise RuntimeError("Configure a SearXNG URL in account setup first.")
    response = requests.get(
        f"{SEARXNG_URL}/search",
        params={
            "q": query,
            "format": "json",
            "categories": "general",
            "language": "en",
            "pageno": 1,
            "safesearch": 0
        },
        headers={
            "User-Agent": "Potack/2.0"
        },
        timeout=15
    )

    response.raise_for_status()

    payload = response.json()

    results = []

    for result in payload.get("results", []):
        title = str(
            result.get("title", "")
        ).strip()

        url = str(
            result.get("url", "")
        ).strip()

        content = str(
            result.get("content", "")
        ).strip()

        if not title or not url:
            continue

        results.append(
            {
                "title": title,
                "url": url,
                "content": content
            }
        )

        if len(results) >= max_results:
            break

    return results


# =========================
# PING
# =========================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.ping$"
    )
)
async def ping(event):

    await event.edit(
        branded("<b>🏓 Pong</b>\n\n"
        "Potack is alive.")
    )


# =========================
# STATUS
# =========================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.status$"
    )
)
async def status(event):

    format_state = format_status_text(
        event.chat_id
    )

    router = (
        "configured"
        if (
            OPENROUTER_API_KEY
            and not OPENROUTER_API_KEY.startswith("PASTE_")
        )
        else "missing key"
    )

    await event.edit(
        branded("<b>⚡ Potack status</b>\n\n"
        f"AI: <code>{router}</code>\n"
        f"AI model: <code>{html.escape(AI_MODEL)}</code>\n"
        f"Web: <code>{html.escape(SEARXNG_URL)}</code>\n"
        f"Format: <code>{html.escape(format_state)}</code>")
    )


# =========================
# FORMAT COMMANDS
# =========================

async def set_format_mode(event, mode, value):

    modes = get_format(event.chat_id)

    modes[mode] = value

    save_data()

    if value:
        await event.edit(
            branded(f"✨ <b>{mode.title()}</b> formatting is now ON.\n\n"
            "It will stay active for your next messages.")
        )
    else:
        await event.edit(
            branded(f"✓ <b>{mode.title()}</b> formatting is OFF."
 )       )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.bold (on|off)$"
    )
)
async def bold_mode(event):

    value = event.pattern_match.group(1) == "on"

    await set_format_mode(
        event,
        "bold",
        value
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.italic (on|off)$"
    )
)
async def italic_mode(event):

    value = event.pattern_match.group(1) == "on"

    await set_format_mode(
        event,
        "italic",
        value
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.underline (on|off)$"
    )
)
async def underline_mode(event):

    value = event.pattern_match.group(1) == "on"

    await set_format_mode(
        event,
        "underline",
        value
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.strike (on|off)$"
    )
)
async def strike_mode(event):

    value = event.pattern_match.group(1) == "on"

    await set_format_mode(
        event,
        "strike",
        value
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.code (on|off)$"
    )
)
async def code_mode(event):

    value = event.pattern_match.group(1) == "on"

    await set_format_mode(
        event,
        "code",
        value
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.format$"
    )
)
async def format_status(event):

    state = format_status_text(
        event.chat_id
    )

    if state == "OFF":

        await event.edit(
            branded("<b>Formatting</b>\n\n"
            "No formatting is active.\n\n"
            "<code>.bold on</code>\n"
            "<code>.italic on</code>\n"
            "<code>.underline on</code>\n"
            "<code>.strike on</code>\n"
            "<code>.code on</code>")
        )

        return

    await event.edit(
        branded("<b>Formatting</b>\n\n"
        f"Active: <code>{html.escape(state)}</code>\n\n"
        "Use <code>.format off</code> to disable everything.")
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.format off$"
    )
)
async def format_off(event):

    modes = get_format(event.chat_id)

    for key in modes:
        modes[key] = False

    save_data()

    await event.edit(
        branded("✓ <b>All formatting disabled.</b>"
 )   )


# =========================
# AUTOMATIC FORMATTER
# =========================

@client.on(
    events.NewMessage(outgoing=True)
)
async def automatic_formatter(event):

    text = event.raw_text or ""

    if not text:
        return

    # Never format Potack commands.
    if text.startswith(".") or is_research_command(text):
        return

    # Never format Qalawa/delete triggers.
    if parse_command(text) is not None:
        return

    modes = get_format(event.chat_id)

    if not any(modes.values()):
        return

    try:
        formatted = apply_format(
            text,
            modes
        )

        if formatted != text:
            await event.edit(
                formatted,
                parse_mode="html"
            )

    except Exception:
        pass


# =========================
# AI
# =========================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.ai (.+)$"
    )
)
async def ai_command(event):

    question = event.pattern_match.group(1).strip()

    if not question:
        await event.edit(
            branded("❌ Give me a question."
 )       )
        return

    await event.edit(
        branded("🧠 <b>Thinking…</b>"
  )  )

    try:

        answer = await asyncio.to_thread(
            ask_ai,
            question
        )

        await event.edit(
            branded(html.escape(answer)),
            parse_mode="html"
        )

    except Exception as error:

        await event.edit(
            branded("❌ <b>AI error</b>\n\n"
            f"<code>{html.escape(type(error).__name__)}</code>")
        )


# =========================
# WEB AI SEARCH
# =========================

async def run_web_search(event, query):

    if not SEARXNG_URL:
        await event.edit(branded("Web search needs a SearXNG URL. Add your HTTPS server in xa account setup."))
        return

    await event.edit(
        branded("🌐 <b>Searching…</b>"
  )  )

    try:

        results = await asyncio.to_thread(
            searxng_search,
            query,
            6
        )

    except Exception as error:

        await event.edit(
            branded("❌ <b>Web search is offline.</b>\n\n"
            "Potack cannot reach SearXNG.\n\n"
            f"<code>{html.escape(type(error).__name__)}</code>\n\n"
            "Try <code>.ai your question</code> instead.")
        )

        return

    if not results:

        await event.edit(
            branded("🔎 No useful search results found."
  )      )

        return

    await event.edit(
        branded("🤖 <b>Reading sources…</b>"
  )  )

    try:

        answer = await asyncio.to_thread(
            summarize_search,
            query,
            results
        )

        await event.edit(
            branded(html.escape(answer)),
            parse_mode="html",
            link_preview=False
        )

    except Exception as error:

        await event.edit(
            branded("❌ <b>AI rewrite failed.</b>\n\n"
            f"<code>{html.escape(type(error).__name__)}</code>\n\n"
            "The search engine itself is working.")
        )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.xa (.+)$"
    )
)
async def xa_search(event):

    query = event.pattern_match.group(1).strip()

    if not query:
        await event.edit(
            branded("❌ What should I search for?"
 )       )
        return

    await run_web_search(
        event,
        query
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.search (.+)$"
    )
)
async def search_command(event):

    query = event.pattern_match.group(1).strip()

    if not query:
        await event.edit(
            branded("❌ What should I search for?"
 )       )
        return

    await run_web_search(
        event,
        query
    )


# =========================
# STORAGE
# =========================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.save ([^ ]+) (.+)$"
    )
)
async def save_info(event):

    key = event.pattern_match.group(1)
    value = event.pattern_match.group(2)

    data["notes"][key] = value

    save_data()

    await event.edit(
        branded("💾 <b>SAVED</b>\n\n"
        f"Key: <code>{html.escape(key)}</code>")
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.get ([^ ]+)$"
    )
)
async def get_info(event):

    key = event.pattern_match.group(1)

    if key not in data["notes"]:

        await event.edit(
            branded(f"❌ Nothing saved under "
            f"<code>{html.escape(key)}</code>")
        )

        return

    value = data["notes"][key]

    await event.edit(
        branded(f"<b>{html.escape(key)}</b>\n\n"
        f"{html.escape(str(value))}")
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.forget ([^ ]+)$"
    )
)
async def forget_info(event):

    key = event.pattern_match.group(1)

    if key not in data["notes"]:

        await event.edit(
            branded(f"❌ <code>{html.escape(key)}</code> "
            "doesn't exist.")
        )

        return

    del data["notes"][key]

    save_data()

    await event.edit(
        branded(f"🗑️ Forgotten "
        f"<code>{html.escape(key)}</code>")
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.notes$"
    )
)
async def notes(event):

    notes = data["notes"]

    if not notes:

        await event.edit(
            branded("📭 <b>No saved information.</b>"
  )      )

        return

    lines = []

    for key in notes:
        lines.append(
            f"• <code>{html.escape(key)}</code>"
        )

    await event.edit(
        branded("<b>💾 Saved notes</b>\n\n"
        + "\n".join(lines))
    )


# =========================
# ACCOUNT
# =========================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.id$"
    )
)
async def show_id(event):

    sender = await event.get_sender()

    await event.edit(
        branded("<b>🆔 Identification</b>\n\n"
        f"Chat ID: <code>{event.chat_id}</code>\n"
        f"Your ID: <code>{sender.id}</code>")
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.me$"
    )
)
async def me(event):

    account = await client.get_me()

    username = (
        f"@{account.username}"
        if account.username
        else "No username"
    )

    name = account.first_name or "Unknown"

    await event.edit(
        branded("<b>👤 Potack account</b>\n\n"
        f"Name: <code>{html.escape(name)}</code>\n"
        f"Username: <code>{html.escape(username)}</code>\n"
        f"ID: <code>{account.id}</code>")
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.about$"
    )
)
async def about(event):

    await event.edit(
        "<b>⚡ Potack</b>\n\n"
        "Personal Telegram toolkit.\n\n"
        "AI\n"
        "🌐 Web search\n"
        "🎨 Persistent formatting\n"
        "💾 Private local notes\n"
        "🧹 Message cleanup\n"
        "🔧 Account tools\n\n"
        "Created by xa"
    )


# =========================
# CLEAR COMMAND
# =========================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.clear$"
    )
)
async def clear_command(event):

    try:
        await event.delete()
    except Exception:
        pass


# =========================
# QALAWA / DELETE
# =========================

cleanup = Cleanup(client, data.setdefault("cleanup", {}), save_data)
research = Research(client, BASE_DIR / "research_data")


@client.on(events.NewMessage(outgoing=True))
async def gift_research(event):
    await research.handle(event)


@client.on(events.NewMessage(outgoing=True))
async def qalawa(event):
    await cleanup.handle(event)


# =========================
# HELP
# =========================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.help$"
    )
)
async def help_command(event):

    await event.edit(
        "<b>⚡ Potack commands</b>\n\n"

        "<b>AI</b>\n"
        "<code>.ai question</code>\n"
        "<code>.xa search</code>\n"
        "<code>.search query</code>\n\n"

        "<b>Formatting</b>\n"
        "<code>.bold on</code>\n"
        "<code>.bold off</code>\n"
        "<code>.italic on</code>\n"
        "<code>.italic off</code>\n"
        "<code>.underline on</code>\n"
        "<code>.underline off</code>\n"
        "<code>.strike on</code>\n"
        "<code>.strike off</code>\n"
        "<code>.code on</code>\n"
        "<code>.code off</code>\n"
        "<code>.format</code>\n"
        "<code>.format off</code>\n\n"

        "<b>Notes</b>\n"
        "<code>.save key value</code>\n"
        "<code>.get key</code>\n"
        "<code>.forget key</code>\n"
        "<code>.notes</code>\n\n"

        "<b>Tools</b>\n"
        "<code>.ping</code>\n"
        "<code>.status</code>\n"
        "<code>.id</code>\n"
        "<code>.me</code>\n"
        "<code>.about</code>\n"
        "<code>.clear</code>\n\n"

        "<b>Gifts and Stars research</b>\n"
        "<code>/gifthelp</code>\n\n"

        "<b>Cleanup</b>\n"
        "<code>Qalawa 100</code>\n"
        "<code>Qalawa preview 100</code>\n"
        "<code>Qalawa cancel</code>\n"
        "<code>Qalawa help</code>\n\n"

        "Created by xa"
    )


# =========================
# START
# =========================

def main():
    print("⚡ Potack — Personal Telegram toolkit")
    print("Created by xa")
    client.start()
    print("Potack is online. Type .help in Telegram.")
    client.loop.run_until_complete(research.start())
    try:
        client.run_until_disconnected()
    finally:
        client.loop.run_until_complete(research.stop())


if __name__ == "__main__":
    main()
