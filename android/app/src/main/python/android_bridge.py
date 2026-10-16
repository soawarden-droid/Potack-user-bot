"""Android-only runtime. All Telethon operations stay on one asyncio loop."""
import asyncio
from concurrent.futures import CancelledError
import importlib
import json
import os
from pathlib import Path
import sys
import threading

_loop = None
_thread = None
_bot = None
_phone = None
_code_hash = None
_running = False
_active_future = None
_active_action = None
_stop_requested = False
_state = {"phase": "offline", "message": "Ready when you are.", "running": False, "busy": False}
_lock = threading.Lock()
_start_lock = threading.Lock()


def _set(phase, message):
    with _lock:
        _state.update(phase=phase, message=message, running=_running)


def snapshot():
    with _lock:
        state = dict(_state)
    if state["running"] and _bot and not _bot.client.is_connected():
        state.update(phase="disconnected", message="Telegram disconnected. If it persists, tap STOP then START to reconnect.")
    return json.dumps(state)


def request_stop():
    """Signal the service queue immediately, without waiting for network I/O."""
    global _stop_requested
    with _lock:
        _stop_requested = True
        _state.update(phase="stopping", message="Stopping the userbot…")
        future = _active_future if _active_action not in ("stop", "shutdown") else None
    if future is not None:
        future.cancel()


def _ensure_loop():
    global _loop, _thread
    with _start_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            def run():
                asyncio.set_event_loop(_loop)
                _loop.run_forever()
            _thread = threading.Thread(target=run, name="xa-telegram", daemon=True)
            _thread.start()


def dispatch(home, action, payload="{}"):
    global _active_future, _active_action, _stop_requested
    _ensure_loop()
    with _lock:
        skip = _stop_requested and action not in ("stop", "shutdown")
        if not skip:
            _state["busy"] = True
    if skip:
        return snapshot()
    future = asyncio.run_coroutine_threadsafe(_dispatch(Path(home), action, json.loads(payload)), _loop)
    with _lock:
        _active_future, _active_action = future, action
        cancel = _stop_requested and action not in ("stop", "shutdown")
    if cancel:
        future.cancel()
    try:
        future.result(timeout=90)
    except CancelledError:
        # A STOP intent interrupts pending authentication or connection work.
        pass
    except TimeoutError:
        future.cancel()
        _set("error", "Connection timed out. Check your network and try again.")
    finally:
        with _lock:
            _active_future, _active_action = None, None
            _state["busy"] = False
            if action == "stop":
                _stop_requested = False
    return snapshot()


async def _disconnect():
    global _running
    _running = False
    if _bot:
        try:
            await _bot.client.set_receive_updates(False)
            await _bot.client.disconnect()
        finally:
            await _bot.research.stop()


async def _dispatch(home, action, values):
    global _bot, _phone, _code_hash, _running
    from telethon import errors
    try:
        if action == "logout":
            await _disconnect()
            if _bot is None and (home / "potack_private.json").exists():
                os.environ["POTACK_HOME"] = str(home)
                _bot = importlib.import_module("main")
            if _bot:
                await _bot.client.connect()
                if not await _bot.client.log_out():
                    raise ValueError("Sign out failed. Check the connection and retry.")
                _bot = None
                sys.modules.pop("main", None)
            (home / "potack_private.json").unlink(missing_ok=True)
            _phone, _code_hash = None, None
            _set("offline", "Signed out. Open account setup to sign in again.")
            return
        if action == "shutdown":
            await _disconnect()
            with _lock:
                _state["running"] = False
                if _state["phase"] not in ("error", "offline"):
                    _state.update(phase="offline", message="Listener stopped. Tap START USERBOT to reconnect.")
            return
        if action == "stop":
            await _disconnect()
            _set("offline", "Userbot stopped. Your login is saved on this phone.")
            return
        if action == "login":
            if _running:
                raise ValueError("Stop the userbot before changing account settings.")
            api_id = int(values.get("api_id", "0"))
            api_hash = values.get("api_hash", "").strip()
            phone = values.get("phone", "").strip()
            if api_id <= 0 or len(api_hash) != 32 or any(c not in "0123456789abcdefABCDEF" for c in api_hash):
                raise ValueError("Enter your Telegram API ID and 32-character API hash.")
            if not phone.startswith("+") or not phone[1:].isdigit():
                raise ValueError("Use an international phone number, beginning with +.")
            search = values.get("search_url", "").strip()
            if search and not search.startswith("https://"):
                raise ValueError("The optional search server must use HTTPS.")
            await _disconnect()
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(home, 0o700)
            config = {"api_id": api_id, "api_hash": api_hash, "session_name": "android",
                      "OPENROUTER_API_KEY": values.get("ai_key", "").strip(), "SEARXNG_URL": search, "android_paused": True}
            temporary = home / "potack_private.json.tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(config, output)
            os.replace(temporary, home / "potack_private.json")
            os.environ["POTACK_HOME"] = str(home)
            # Reload only after disconnecting: decorators bind to the new client.
            if _bot is None:
                _bot = importlib.import_module("main")
            else:
                _bot = importlib.reload(_bot)
            _phone, _code_hash = phone, None
            _set("connecting", "Connecting to Telegram…")
            await _bot.client.connect()
            if await _bot.client.is_user_authorized():
                account = await _bot.client.get_me()
                if account.phone != phone.lstrip("+"):
                    raise ValueError("This phone has another saved account. Sign out before changing accounts.")
                _set("ready", "Login restored. Tap START USERBOT.")
            else:
                sent = await _bot.client.send_code_request(phone)
                _code_hash = sent.phone_code_hash
                _set("code", "Telegram sent a login code. Enter it below.")
        elif action == "code":
            if _bot is None or not _phone or not _code_hash:
                raise ValueError("Request a login code first.")
            code = values.get("code", "").strip()
            if not code or not code.isascii() or not code.isdigit():
                _set("code", "Enter the numeric Telegram login code.")
                return
            await _bot.client.sign_in(_phone, code, phone_code_hash=_code_hash)
            _code_hash = None
            _set("ready", "Signed in. Tap START USERBOT.")
        elif action == "password":
            if _bot is None:
                raise ValueError("Begin Telegram login first.")
            password = values.get("password", "")
            if not password:
                _set("password", "Enter your Telegram two-step password.")
                return
            await _bot.client.sign_in(password=password)
            _code_hash = None
            _set("ready", "Signed in. Tap START USERBOT.")
        elif action == "start":
            if _bot is None:
                config = home / "potack_private.json"
                if not config.exists():
                    raise ValueError("Set up your Telegram login first.")
                os.environ["POTACK_HOME"] = str(home)
                _bot = importlib.import_module("main")
            await _bot.client.connect()
            if not await _bot.client.is_user_authorized():
                raise ValueError("Sign in to Telegram first.")
            await _bot.client.set_receive_updates(True)
            await _bot.research.start()
            _running = True
            _set("online", "Listening for your commands in Telegram.")
        else:
            raise ValueError("Unknown action.")
    except errors.SessionPasswordNeededError:
        _set("password", "Two-step verification is enabled. Enter your Telegram password.")
    except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
        _set("code", "Code invalid or expired. Retry, or request a new code.")
    except errors.PasswordHashInvalidError:
        _set("password", "Incorrect Telegram password. Try again.")
    except errors.FloodWaitError as error:
        _set("error", f"Telegram asks you to wait {error.seconds} seconds before retrying.")
    except ValueError as error:
        # Only our validation text; avoid showing third-party exception payloads.
        message = str(error)
        allowed = ("Enter your", "Use an", "The optional", "Stop the", "Request a", "Begin Telegram", "Set up", "Sign in", "Sign out", "This phone", "Unknown")
        _set("error", message if message.startswith(allowed) else "Check your account settings and try again.")
    except Exception:
        _set("error", "Telegram connection failed. Check your network and account settings, then retry.")
