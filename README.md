# Klipper Telegram · v0.2.0 (preview)

Small read-only Telegram notification service for Moonraker / Klipper, with user pairing and a local systemd service.

## Features

- Dedicated Telegram BotFather token per printer
- Subscription through `/subscribe ABCD-EFGH` or Telegram `/start ABCD-EFGH`
- Single-use 8-character pairing code; default TTL 600 seconds
- `TELEGRAM_PAIRING_CREATE` and `TELEGRAM_STATUS` G-Code macros
- Expiration and successful subscriber notifications in Mainsail console
- SQLite subscriber database and printer UUID
- Configurable `[on_printing]`, `[on_paused]`, `[on_complete]`, `[on_cancelled]`, `[on_error]` hooks
- Read-only `/status`, `/unsubscribe`, `/help`
- No centralized backend; the bot talks directly to Telegram and Moonraker

## Requirements

Linux with Python 3, python3-venv, systemd, Moonraker and Klipper, `make`, `git`, plus network access to Telegram. Tested syntax and local unit tests; **not yet tested on a physical printer**.

## Install

Clone the public GitHub repository into the printer user's home folder, then:

```bash
cd ~/klipper-telegram
make install
```

The installer prints seven numbered phases, reports each action and skip, shows the systemd service state, and finishes with an installation summary. It asks whether to create the default `.conf`, add the Telegram token, install G-Code macros and configure the Moonraker `git_repo` update manager. The public GitHub `origin` must already be present for update-manager setup. It registers and enables a `klipper-telegram` systemd service. Re-running the installer preserves local configuration files.

**Do not run `sudo make install`.** The installer only elevates the systemd unit registration. Dependencies install into `~/klipper-telegram-env`.

Setup files:

- `~/printer_data/config/klipper_telegram.conf`
- `~/printer_data/config/klipper_telegram.token` (secret, chmod 600)
- `~/printer_data/config/telegram_macros.cfg` (optional)
- `~/printer_data/config/printer.cfg` includes the macro (optional)
- `~/printer_data/config/subscribers.db` (local private state)

## Quick start

1. Create a bot using Telegram BotFather and set its token in the installer or the token file.
2. Run `sudo systemctl restart klipper-telegram`.
3. Restart Klipper if you added `telegram_macros.cfg`.
4. On the printer host, run `~/klipper-telegram-env/bin/python -m klipper_telegram --config ~/printer_data/config/klipper_telegram.conf pairing arm` from the repository directory (or `PYTHONPATH` pointing to it). Within 60 seconds run `TELEGRAM_PAIRING_CREATE` from Mainsail. The authorization is single-use.
5. In a **private** chat with the bot, run `/subscribe ABCD-EFGH` replacing the code.
6. The console should report `Connected to Telegram-User @username` or `User <id>`.
7. Run `TELEGRAM_STATUS` to see connection/subscriber information.

For SSH: `PYTHONPATH=. python3 -m klipper_telegram --config ~/printer_data/config/klipper_telegram.conf pairing create` (needs the installed environment if `aiohttp` isn't installed). CLI generation uses the same database, but for full console notifications prefer the G-Code macro.

## Security

**Important:** Any G-Code source that can execute `TELEGRAM_PAIRING_CREATE` can generate a pairing credential. Do not enable this macro on printers that receive G-Code from untrusted sources. This preview doesn't enforce administrator-only invocation; Klipper does not natively distinguish a human console command from a command embedded in a print file.

Pairing codes are hashed before storage, single-use and rate-limited per Telegram user ID; chats must be private. The Telegram token must never be committed to Git. Printing a pairing code in the Mainsail console is visible to authorized dashboard users and potentially console histories. The console output is not an authorization boundary.

## Limitations / preview status

- No end-to-end printer/Moonraker integration tests yet; validate on a spare system before production.
- Printing events are derived from `print_stats.state` transitions; state changes missed during offline periods cannot be reconstructed.
- Hook delivery isn't queued across service restarts and uses best-effort network delivery.
- The daemon and CLI share SQLite but an independently generated CLI code doesn't cause an immediate Mainsail console message.
- If Moonraker is down when pairing expires, the console message is not retried; the code still expires.
- Standard Telegram Bot API long-polling: **do not configure a webhook for the same bot**.
- Moonraker must allow localhost requests (trusted clients) or configure an API key.

## Updating via Moonraker

Clone using `git clone https://github.com/D3ADD3V3R/KlipperTelegramBot.git` and run `make install`; choose Update Manager registration. Moonraker manages the Git repository and the `klipper-telegram` service, while credentials and SQLite remain under `~/printer_data/config/`. The `dev` channel tracks the latest commit on `main`; create a semantic version tag such as `v0.2.0` before switching to stable releases.

## License

MIT. See [LICENSE](LICENSE).

## Development

```bash
make test
```
