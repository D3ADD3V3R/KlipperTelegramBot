# KlipperTelegramBot 0.4.0

Telegram notifications and secure pairing for Klipper/Moonraker.

**Preview: no live Raspberry Pi integration test has been performed.** Do not install during a print. The old v0.3.x Moonraker agent instability has not been conclusively regression-tested on real hardware.

## Install

Clone the repository (Git checkout required for Moonraker Update Manager), then `make install` as the ordinary printer user. The installer asks about config, token, macros, Update Manager and service restarts. It never edits Klipper's SAVE_CONFIG block; it places the include before that marker.

`make uninstall` removes systemd and optionally macros/update integration. Private data are retained by default.

Moonraker `git_repo` updates are **noninteractive**: they update Git, install requirements, and restart the managed Telegram service, **without invoking `make install`**. If you change systemd unit or macro definitions, rerun `make install` manually when idle.

## Telegram commands

* `/start`, `/help`, `/subscribe CODE`, `/unsubscribe`
* `/status`, `/notifications`
* `/image` (default camera), `/image PrinterCam` (named camera), `/image list`

Command menus are registered automatically with Telegram and subscriber-private scopes after pairing. A menu is NOT an authorization boundary: every command checks the subscriber store.

Webcam metadata comes from Moonraker `/server/webcams/list`. Images are fetched from Crowsnest snapshot URLs. Remote hosts must be explicitly listed under `[image] allowed_hosts`, redirection is disallowed, and images are limited in size and type. Camera `snapshot_url` must return JPEG/PNG, not MJPEG stream.

Hook sections e.g. `[on_complete]` support `message`, `enabled`, `include_image: true`, `camera: PrinterCam`.

## Security

Bot token is stored only in a local chmod-600 file. Pairing is gated by CLI `klipper-telegram pairing arm` before `TELEGRAM_PAIRING_CREATE`; tokens are not printed in G-code. The store uses Moonraker namespace `klipper_telegram`; migration of legacy SQLite is read-only.

## Compatibility note

`python-telegram-bot` handles Telegram polling, command registration and sending. The specialized Moonraker agent websocket remains on `aiohttp`: `pymoonraker` does not currently expose a documented public interface for incoming registered Agent Remote Methods. There is only one Moonraker agent WebSocket reader, and RPC replies are correlated by request id. Standard database operations use Moonraker's REST API. A future release can adopt more `pymoonraker` APIs after integration verification.

## Diagnostics

`systemctl status klipper-telegram` and `journalctl -u klipper-telegram -n 100 --no-pager`. Update Manager status: `/machine/update/status`.

## License

MIT. See [LICENSE](LICENSE).

## Extended `/status` output

`/status` now queries live Moonraker printer objects when requested. During a
print it displays filename, active print duration, estimated remaining time,
current/total layer, and progress. After completion it displays filename,
total job duration (including paused time), and filament used (meters).

Remaining time is **estimated**, not a native Klipper `time_to_finish` field:
Moonraker G-code file metadata `estimated_time` is combined with current print
progress. When metadata, slicer layer commands, or another value is unavailable,
`–` is displayed. For layer counts, configure the slicer to send
`SET_PRINT_STATS_INFO TOTAL_LAYER=...` and `CURRENT_LAYER=...` using its own
placeholder syntax. Existing hook messages are unchanged.

Only the `klipper-telegram` service needs restarting after updating; no Klipper
restart is needed. Restarting the standalone service may cause a brief gap in
Telegram notifications but normally does not interrupt an ongoing print.
