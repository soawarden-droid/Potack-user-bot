"""Account-only cleanup; no network calls at import time."""
import asyncio
from dataclasses import dataclass, field
import re
import time
from telethon.errors import FloodWaitError, MessageNotModifiedError

SIGNATURE = 'Created by xa'
ALIASES = {'qalawa', 'awghan', 'uzbek', 'tajik', 'blue moon', 'baby wipe',
           'delete', 'remove', 'kancha', 'fck', 'black', 'white', 'red', 'wardak', 'deh'}
MAX_COUNT = 10000
HELP = '''<b>Potack cleanup</b>
<code>Qalawa 100</code> — latest 100 eligible messages before this command.
<code>Qalawa preview 100</code> — count eligible messages without deleting.
<code>Qalawa cancel</code> — stop this chat's current job.
<code>Qalawa</code> — all your eligible messages before this command.
Reply to a message with Qalawa to clean from that message through this command (inclusive of the replied message). A count caps that range. Preview without a count scans the same range.
Only messages authored by your account are eligible. Commands, progress, service messages, and messages sent as a channel are excluded.
<code>Qalawa autodelete 10</code> — remove final cleanup statuses after 10 seconds; use <code>off</code> to keep them. Range: 1–300 seconds.
Legacy aliases still work as bare commands. No dot prefix is needed.'''


def branded(text):
    return text.rstrip() + '\n\n' + SIGNATURE


def parse_command(text):
    text = text.strip().casefold()
    if text in ALIASES:
        return ('clean', None)
    if not re.match(r'^qalawa(?:\s|$)', text):
        return None
    parts = text.split()
    if parts == ['qalawa', 'help']:
        return ('help', None)
    if parts == ['qalawa', 'cancel']:
        return ('cancel', None)
    if len(parts) == 3 and parts[1] == 'autodelete':
        if parts[2] == 'off':
            return ('setting', 0)
        if re.fullmatch(r'[0-9]{1,3}', parts[2]) and 1 <= int(parts[2]) <= 300:
            return ('setting', int(parts[2]))
        return ('invalid', None)
    action = 'preview' if len(parts) > 1 and parts[1] == 'preview' else 'clean'
    numbers = parts[2:] if action == 'preview' else parts[1:]
    if not numbers and action == 'preview':
        return (action, None)
    if len(numbers) == 1 and re.fullmatch(r'[0-9]{1,5}', numbers[0]):
        count = int(numbers[0])
        if 1 <= count <= MAX_COUNT:
            return (action, count)
    return ('invalid', None)


class Stopped(Exception):
    pass


@dataclass
class Job:
    event: object
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    started: float = field(default_factory=time.monotonic)
    last_edit: float = 0
    status_wait_until: float = 0
    selected: int = 0
    eligible: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: int = 0
    unconfirmed: int = 0
    last_status: str = ''
    status_errors: int = 0


class Cleanup:
    def __init__(self, client, settings, save):
        self.client = client
        self.settings = settings
        self.save = save
        self.jobs = {}
        self.protected = {}  # Control messages stay excluded from subsequent scans.
        self.timers = set()

    async def edit(self, job, text, force=False):
        now = time.monotonic()
        if now < job.status_wait_until or (not force and now - job.last_edit < 2):
            return
        job.last_edit = now
        job.last_status = text
        try:
            await job.event.edit(branded(text), parse_mode='html', link_preview=False)
        except MessageNotModifiedError:
            pass
        except FloodWaitError as error:
            job.status_wait_until = time.monotonic() + error.seconds
        except Exception:
            job.status_errors += 1

    async def wait(self, job, seconds):
        try:
            await asyncio.wait_for(job.cancel.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
        raise Stopped

    async def call(self, job, operation, cancellable=True):
        while not cancellable or not job.cancel.is_set():
            try:
                return await operation()
            except FloodWaitError as error:
                await self.edit(job, '⏳ Potack is waiting…\n'
                                f'Telegram rate limit: {error.seconds} seconds\n'
                                f'Deleted: {job.deleted}', force=True)
                if cancellable:
                    await self.wait(job, error.seconds)
                else:
                    await asyncio.sleep(error.seconds)
        raise Stopped

    def eligible(self, message, me, protected):
        return (message is not None and message.sender_id == me
                and not getattr(message, 'action', None)
                and message.id not in protected)

    async def delete_batch(self, job, chat, ids, me, protected):
        # Recheck authorship immediately before every bounded delete request.
        messages = await self.call(job, lambda: self.client.get_messages(chat, ids=ids))
        safe = [m.id for m in messages if self.eligible(m, me, protected)]
        job.skipped += len(ids) - len(safe)
        if not safe:
            return
        try:
            await self.call(job, lambda: self.client.delete_messages(chat, safe, revoke=True))
        except Stopped:
            raise
        except Exception:
            pass  # Verify even if a request timed out after reaching Telegram.
        # Cancellation cannot interrupt accounting for a request already sent.
        # Verify disappearance; Telegram may silently leave undeletable messages.
        try:
            remaining = await self.call(job, lambda: self.client.get_messages(chat, ids=safe), cancellable=False)
            present = {m.id for m in remaining if m is not None}
            job.failed += len(present)
            job.deleted += len(safe) - len(present)
        except Exception:
            job.unconfirmed += len(safe)

    async def expire(self, event, delay):
        await asyncio.sleep(delay)
        try:
            await event.delete()
        except Exception:
            pass

    async def handle(self, event):
        parsed = parse_command(event.raw_text or '')
        if parsed is None:
            return
        action, count = parsed
        chat = event.chat_id
        protected = self.protected.setdefault(chat, set())
        # Bound retention while keeping every command newer than any active scan.
        if len(protected) > 2000 and chat not in self.jobs:
            protected.clear()
        protected.add(event.id)
        if action == 'cancel':
            job = self.jobs.get(chat)
            if job:
                job.cancel.set()
            await event.edit(branded('Cleanup cancellation requested.' if job else 'No cleanup is running in this chat.'), parse_mode='html')
            return
        if action in {'help', 'invalid', 'setting'}:
            text = HELP
            if action == 'invalid':
                text = 'Invalid cleanup command. Count must be 1–10000.\n\n' + HELP
            elif action == 'setting':
                self.settings['final_status_seconds'] = count
                self.save()
                text = f'Final cleanup statuses will disappear after {count} seconds.' if count else 'Final cleanup statuses will stay in the chat.'
            await event.edit(branded(text), parse_mode='html')
            return
        if chat in self.jobs:
            await event.edit(branded('A cleanup is already running in this chat. Use Qalawa cancel to stop it.'), parse_mode='html')
            return
        job = Job(event)
        self.jobs[chat] = job  # Reserve synchronously before the first await.
        outcome = 'complete'
        try:
            await self.edit(job, '⚡ Potack is working…\n' + ('Previewing your messages in this chat.\nEligible: 0' if action == 'preview' else 'Cleaning your messages in this chat.\nDeleted: 0 / ' + (str(count) if count else 'all eligible')), force=True)
            me = (await self.call(job, self.client.get_me)).id
            lower = event.reply_to_msg_id or 1
            cursor = event.id
            while not job.cancel.is_set():
                # Use ordinary history, avoiding search filter pagination quirks.
                page = await self.call(job, lambda: self.client.get_messages(chat, limit=100, max_id=cursor, min_id=lower-1))
                if not page:
                    break
                eligible = [m.id for m in page if lower <= m.id < event.id and self.eligible(m, me, protected)]
                if count is not None:
                    eligible = eligible[:count-job.selected]
                job.eligible += len(eligible)
                job.selected += len(eligible)
                if action == 'clean':
                    for offset in range(0, len(eligible), 50):
                        await self.delete_batch(job, chat, eligible[offset:offset+50], me, protected)
                        await self.edit(job, f'⚡ Potack is working…\nCleaning your messages in this chat.\nDeleted: {job.deleted} / {count if count else job.selected}\nFailed: {job.failed} · Skipped: {job.skipped} · Unconfirmed: {job.unconfirmed}')
                        await self.wait(job, 0.5)
                else:
                    await self.edit(job, f'⚡ Potack is working…\nPreviewing your messages in this chat.\nEligible: {job.eligible}')
                next_cursor = min(m.id for m in page)
                if next_cursor >= cursor:
                    raise RuntimeError('History pagination did not advance')
                cursor = next_cursor
                if count is not None and job.selected >= count:
                    break
            if job.cancel.is_set():
                outcome = 'cancelled'
        except Stopped:
            outcome = 'cancelled'
        except Exception:
            outcome = 'partial'
        finally:
            if job.failed or job.unconfirmed:
                outcome = 'partial' if outcome == 'complete' else outcome
            elapsed = round(time.monotonic() - job.started)
            if action == 'preview':
                text = f'✨ Preview {outcome}\nEligible: {job.eligible}'
                if count:
                    text += f' / {count}'
                text += '\nNo messages deleted.'
            else:
                title = {'complete': '✨ Cleanup complete', 'cancelled': 'Cleanup cancelled', 'partial': 'Cleanup partially completed'}[outcome]
                text = f'{title}\nDeleted: {job.deleted} messages'
                if count is not None and job.selected < count:
                    text += f'\nSelected: {job.selected} / {count}'
                if job.failed or job.skipped or job.unconfirmed:
                    text += f'\nFailed: {job.failed}\nSkipped: {job.skipped}\nUnconfirmed: {job.unconfirmed}'
            if action == 'clean':
                pending = job.selected - job.deleted - job.failed - job.skipped - job.unconfirmed
                if pending:
                    text += f'\nNot attempted: {pending}'
            text += f'\nTime: {elapsed} seconds'
            if outcome == 'partial':
                text += '\nSome operations failed; remaining messages were left untouched.'
            # Final updates honor edit rate limits and keep chat reserved until delivered.
            try:
                for _ in range(3):
                    if job.status_wait_until > time.monotonic():
                        await asyncio.sleep(job.status_wait_until-time.monotonic())
                    await self.edit(job, text, force=True)
                    if job.status_wait_until <= time.monotonic():
                        break
            finally:
                self.jobs.pop(chat, None)
            delay = self.settings.get('final_status_seconds', 0)
            if delay and outcome == 'complete':
                timer = asyncio.create_task(self.expire(event, delay))
                self.timers.add(timer)
                timer.add_done_callback(self.timers.discard)
