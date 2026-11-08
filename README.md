# Klipper Telegram · v0.3.0 (preview)

Small read-only Telegram notification service for Moonraker / Klipper, with user pairing and a local systemd service.

## Features

- Dedicated Telegram BotFather token per printer
- Subscription through `/subscribe ABCD-EFGH` or Telegram `/start ABCD-EFGH`
- Single-use 8-character pairing code; default TTL 600 seconds
- `TELEGRAM_PAIRING_CREATE` and `TELEGRAM_STATUS` G-Code macros
- Expiration and successful subscriber notifications in Mainsail console
- Persistent subscriber database, printer UUID and pairing in Moonraker namespace `klipper_telegram`
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

The installer installs an editable Python package and the `klipper-telegram` CLI into the virtual environment, and adds a guarded global symlink under `/usr/local/bin`. It prints seven numbered phases, reports each action and skip, shows the systemd service state, and finishes with an installation summary. It asks whether to create the default `.conf`, add the Telegram token, install G-Code macros and configure the Moonraker `git_repo` update manager. The public GitHub `origin` must already be present for update-manager setup. It registers and enables a `klipper-telegram` systemd service. Re-running the installer preserves local configuration files.

**Do not run `sudo make install`.** The installer only elevates the systemd unit registration. Dependencies install into `~/klipper-telegram-env`.

Setup files:

- `~/printer_data/config/klipper_telegram.conf`
- `~/printer_data/config/klipper_telegram.token` (secret, chmod 600)
- `~/printer_data/config/telegram_macros.cfg` (optional)
- `~/printer_data/config/printer.cfg` includes the macro (optional)
- Moonraker namespace `klipper_telegram` (persistent data, managed by Moonraker)
- `~/printer_data/config/subscribers.db` (legacy database; imported read-only once, never deleted automatically)

## Quick start

1. Create a bot using Telegram BotFather and set its token in the installer or the token file.
2. Run `sudo systemctl restart klipper-telegram`.
3. Restart Klipper if you added `telegram_macros.cfg`.
4. On the printer host, run `klipper-telegram pairing arm` (or `~/klipper-telegram-env/bin/klipper-telegram pairing arm`). Within 60 seconds run `TELEGRAM_PAIRING_CREATE` from Mainsail. The authorization is single-use.
5. In a **private** chat with the bot, run `/subscribe ABCD-EFGH` replacing the code.
6. The console should report `Connected to Telegram-User @username` or `User <id>`.
7. Run `TELEGRAM_STATUS` to see connection/subscriber information.

For SSH: `PYTHONPATH=. python3 -m klipper_telegram --config ~/printer_data/config/klipper_telegram.conf pairing create` (needs the installed environment if `aiohttp` isn't installed). CLI generation uses the same Moonraker namespace, but for full console notifications prefer the G-Code macro.

## Security

**Important:** Any G-Code source that can execute `TELEGRAM_PAIRING_CREATE` can generate a pairing credential. Do not enable this macro on printers that receive G-Code from untrusted sources. This preview doesn't enforce administrator-only invocation; Klipper does not natively distinguish a human console command from a command embedded in a print file.

Pairing codes are hashed before storage, single-use and rate-limited per Telegram user ID; chats must be private. The Telegram token must never be committed to Git. Printing a pairing code in the Mainsail console is visible to authorized dashboard users and potentially console histories. The console output is not an authorization boundary.

## Limitations / preview status

- No end-to-end printer/Moonraker integration tests yet; validate on a spare system before production.
- Printing events are derived from `print_stats.state` transitions; state changes missed during offline periods cannot be reconstructed.
- Hook delivery isn't queued across service restarts and uses best-effort network delivery.
- The daemon and CLI share a Moonraker database namespace, but an independently generated CLI code doesn't cause an immediate Mainsail console message.
- If Moonraker is down when pairing expires, the console message is not retried; the code still expires.
- Standard Telegram Bot API long-polling: **do not configure a webhook for the same bot**.
- Moonraker must allow localhost requests (trusted clients) or configure an API key.

## Updating via Moonraker

Clone using `git clone https://github.com/D3ADD3V3R/KlipperTelegramBot.git` and run `make install`; choose Update Manager registration. Moonraker manages the Git repository and the `klipper-telegram` service, while credentials remain under `~/printer_data/config/`; subscribers and pairing data live in Moonraker. The `dev` channel tracks the latest commit on `main`; create a semantic version tag such as `v0.2.0` before switching to stable releases.

## License

MIT. See [LICENSE](LICENSE).

## Development

```bash
make test
```

## Uninstall

Run from the repository as the ordinary printer user (do not use `sudo make uninstall`):

```bash
make uninstall
```

The interactive uninstaller stops/disables/removes the `klipper-telegram` systemd unit. It optionally removes Klipper macros, the Moonraker Update Manager entry and `moonraker.asvc` authorization, and the dedicated Python virtual environment. **Configuration, BotFather token, Moonraker namespace, legacy subscriber database and Git repository are preserved by default.** Existing configuration files are backed up before modification. If you remove Moonraker or Klipper integrations, restart the corresponding service afterward.

## Migrating from v0.2.0

Stop the previous `klipper-telegram` service before upgrading. On the first startup,
v0.3.0 reads the old `~/printer_data/config/subscribers.db` (if it exists),
imports existing subscriber records and the printer ID into Moonraker's
`klipper_telegram` namespace and sets a `legacy_migrated` flag. The original
file is **never deleted**. Do not remove it until you have checked the imported
subscriptions and made a backup.

Inspect data (from the printer host):

```bash
curl -s 'http://127.0.0.1:7125/server/database/item?namespace=klipper_telegram'
```

Moonraker authorization must permit the request. The namespace is not a secrets
vault: trusted Moonraker clients can read its values. The Telegram token remains
in its protected local file. Pairing codes are only stored as SHA-256 hashes.

**Preview limitation:** v0.3.0 converts the persistence layer to Moonraker's
public database API, but still uses the existing agent WebSocket for incoming
remote G-Code methods. While `pymoonraker` 0.1.0 supports the full set of
database methods, its documented client does not expose handling for server-
initiated agent methods. Avoiding a second competing WebSocket client takes
precedence over replacing that remaining adapter. Live hardware integration
tests are still required before re-enabling the service for printing.

## CLI and Update Manager troubleshooting (v0.3.0 installer fix)

- Test the CLI with `klipper-telegram --help` or `~/klipper-telegram-env/bin/klipper-telegram --help`.
- The installer uses a guarded symlink to `/usr/local/bin/klipper-telegram` and does not replace foreign binaries.
- An Update Manager `git_repo` requires a real Git checkout, not an unpacked ZIP directory.
- The installer preserves existing `[update_manager klipper-telegram]` configuration, ensures service authorization in `~/printer_data/moonraker.asvc`, and offers to restart Moonraker.
- If updates remain absent, inspect `/server/update/status` and `journalctl -u moonraker -n 80`.
- Uninstall removes only an installer-owned global CLI symlink, not a foreign command.
