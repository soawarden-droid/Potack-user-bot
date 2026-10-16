# Potack

Personal Telegram userbot using your own account, Python 3.12, and Telethon.

See [RESEARCH.md](RESEARCH.md) for the new Telegram Gifts and Stars commands, read-only policy, local cache, exports, Ubuntu startup instructions, and validation. The Gifts and Stars upgrade is active.

Created by xa

Android APK source and setup: [xa · Potack for Android](android/README.md).

The active entry point is `main.py`. A running process in this directory identified it as the active copy; Downloads contains older variants. Existing AI, search, formatting, notes, and account commands are retained.

## Cleanup

Send these as messages from your account; no dot is required:

- `Qalawa 100`: latest 100 eligible messages before the command in this chat.
- `Qalawa preview 100`: count up to 100 eligible messages, without deleting.
- `Qalawa cancel`: cooperatively stop the active job in this chat. An in-flight deletion finishes and is accounted for before stopping.
- `Qalawa help`: full usage.
- `Qalawa`: all eligible earlier messages in this chat.
- `Qalawa preview`: preview all eligible earlier messages.
- `Qalawa autodelete 10`: remove successful final cleanup or preview statuses after 10 seconds. Use `off` to retain statuses (default); allowed delay 1–300 seconds. Failure and cancellation reports are retained.

Counts must be integers from 1 to 10000. Invalid Qalawa commands show help rather than trigger deletion. Legacy bare aliases remain supported: awghan, uzbek, tajik, blue moon, baby wipe, delete, remove, kancha, fck, black, white, red, wardak, deh. Normal messages beginning with other words do not trigger cleanup.

### Reply behavior before this upgrade

The original handler never inspected replies: replying with Qalawa had exactly the same effect as sending it alone. It scanned all your messages in supergroups/channels, attempted each deletion individually, swallowed errors, and could delete its own status. Basic groups and private chats were rejected.

### Reply behavior after this upgrade

Replying with Qalawa limits cleanup to the replied message and later messages before the command, including the replied message if authored by you. A supplied count caps the latest eligible messages in that range. Preview uses identical selection. Qalawa without a reply preserves full-history cleanup. Private chats and basic groups are now supported as well.

Eligibility requires the sender ID to equal your account ID. Channel-authored and service messages are excluded even if your account can delete them. The command and active progress/control messages are excluded. Messages newer than the command are never selected. Other people's messages never enter deletion requests. Ownership is checked again before deletion.

History is fetched in pages of at most 100. Deletes contain at most 50 IDs with a pause between batches. One command message becomes the progress and final status; progress updates are throttled to two seconds. Telegram FloodWait delays are honored with a waiting state; cancellation interrupts waits before new deletions. Different chats can have separate jobs; one chat cannot overlap jobs.

Deleted counts are verified by fetching IDs after deletion. IDs still present count as failed; messages no longer eligible at recheck count as skipped. If verification cannot complete, counts are unconfirmed rather than claimed as deleted. Cancellation and interrupted scans display partial results. Telegram/network failures may prevent status delivery. Deletions themselves cannot be undone by the code backup.

## Existing commands

`.ping`, `.status`, `.help`, `.about`, `.id`, `.me`, `.clear`.

AI: `.ai question`. Web search through local SearXNG: `.xa query`, `.search query`.

Formatting: `.bold on|off`, `.italic on|off`, `.underline on|off`, `.strike on|off`, `.code on|off`, `.format`, `.format off`. Formatting continues to apply to your ordinary outgoing messages; cleanup commands are excluded.

Notes: `.save key value`, `.get key`, `.forget key`, `.notes`.

## Configuration and private files

`potack_private.json` stores the existing Telegram credentials, existing session name, and OpenRouter key; it is restricted to owner read/write and ignored by Git. Credentials were moved without changing their values. Do not share this file or session files. Sessions and existing `potack_data.json` notes were retained in place and excluded from code backups and Git. `potack_data.json` also holds formatting and cleanup settings. Paths are anchored to this directory, so starting from another directory uses the same files.

`main.py` retains nonsecret AI model, OpenRouter URL, and SearXNG URL configuration. The existing environment is `venv`; exact direct dependency versions are recorded in `requirements.txt`.

Run from this directory: `venv/bin/python main.py`. Stop an existing process before starting another so the same Telegram session is not used concurrently.

## Tests and recovery

Run `venv/bin/python -m unittest discover -s tests -v`. These use a fake Telegram client and do not connect to Telegram or delete real messages.

`backups/before-upgrade-*` contains a recoverable original `main.py` with credential values replaced by reads from the retained private configuration. Read its `RESTORE.txt` for restoration. It contains no sessions, notes, or keys. No existing Downloads copies were edited. No AGENTS.md instructions were found in the applicable ancestor directories or project.
