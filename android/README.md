# xa · Potack for Android

Native Android controller with a dark terminal interface, green accents and an xa launcher icon. The APK embeds Python and the existing Telethon userbot using [Chaquopy](https://chaquo.com/chaquopy/doc/current/android.html). Commands still go through the Telegram app, from your own account.

Android 8.0+ (API 26), with an ARM64 phone or x86_64 emulator. It does not support 32-bit-only phones.

## Use

1. Install `dist/xa-potack-debug.apk` from the project root once built. Android may ask you to allow installation from your file manager.
2. Open **xa · Potack**. Enter your own API ID and hash from https://my.telegram.org/apps and your international phone number.
3. Tap **CONNECT / SEND LOGIN CODE**. Enter the code delivered by Telegram and your two-step password if requested.
4. Tap **START USERBOT**. Open Telegram and send `.ping` or `/gifthelp` from your account. Start is required before command handlers receive updates.
5. Tap **STOP** in xa or its notification to disconnect. Reopen xa and tap Start to reconnect with the saved login.

Version 0.1.1 lets **STOP** interrupt a pending login or connection request immediately and discard queued starts until the disconnect finishes. The research worker is also stopped if connection teardown reports an error. Install this APK over version 0.1.0 to retain your phone login and local notes.

**SIGN OUT OF TELEGRAM** revokes the phone session and removes its saved API configuration. Local notes and research history stay on the phone until you clear app storage.

The notification keeps the user-started listener visible. Android or a phone manufacturer's battery manager can still terminate the process; the app does not promise uninterrupted hosting or auto-start on boot. Reopen it and Start after a phone restart. Foreground services follow [Android's service-type rules](https://developer.android.com/develop/background-work/services/fgs/service-types).

Run one Potack instance per Telegram account to avoid duplicate command handling; stop the desktop userbot while using the phone version.

AI requires your own optional OpenRouter key. Web search requires your own HTTPS SearXNG URL; the desktop localhost server is not available on a phone. All other existing commands retain their behavior, including the ability to delete your own messages when you explicitly invoke cleanup.

## Build

Open this directory in Android Studio, install Android SDK Platform 35 and Build Tools 35.0.0, then build the `app` debug APK. Java 17+ and host Python 3.12 are required; Java 21 also works with Gradle 8.13. Build configuration pins AGP 8.9.2 and Chaquopy 17.0.0.

With the Android SDK configured through `ANDROID_HOME` or `local.properties`:

```bash
cd android
./gradlew assembleDebug
mkdir -p ../dist
cp app/build/outputs/apk/debug/app-debug.apk ../dist/xa-potack-debug.apk
```

The Gradle `syncUserbot` task copies only six named Python source files from the parent project into generated build inputs. The Android bridge lives under `app/src/main/python`. Desktop credentials, sessions, notes, research data, backups and logs are excluded. Do not point a Python source set at the whole parent directory.

The small [pyaes wheel](wheels/README.md) supplies Telethon's pure-Python AES dependency in the format required by the Android packager. Its source and wheel hashes and MIT license location are documented alongside it.

The debug APK is suitable for personal testing. Keep a stable private signing key if you later distribute release updates; changing signing keys requires uninstalling and loses the app's local data.

## Storage and validation

Phone configuration, session, notes and research database live in Android private internal app storage. Backups are disabled. Login codes and two-step passwords are not written to settings. The app uses a fresh phone session, never the desktop session. Uninstalling or clearing app storage removes this local data; Telegram's Devices screen can revoke the phone login.

Offline bridge tests use a fake client and cover login, two-step verification, private configuration, paused updates, unauthorized starts and stopping. Run the whole suite from the project root:

```bash
venv/bin/python -m unittest discover -s tests -v
```

Building an APK does not verify login or device background behavior. Those require an emulator or physical phone and the account owner's own login.
