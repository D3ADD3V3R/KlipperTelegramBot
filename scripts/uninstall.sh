#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$EUID" -eq 0 ]]; then
  printf 'ERROR: Run make uninstall as your normal Klipper user, not with sudo.\n' >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRINTER_DATA="${PRINTER_DATA:-$HOME/printer_data}"
CONFIG_DIR="$PRINTER_DATA/config"
MOONRAKER_CONF="$CONFIG_DIR/moonraker.conf"
PRINTER_CONF="$CONFIG_DIR/printer.cfg"
MACROS_FILE="$CONFIG_DIR/telegram_macros.cfg"
CONFIG_FILE="$CONFIG_DIR/klipper_telegram.conf"
TOKEN_FILE="$CONFIG_DIR/klipper_telegram.token"
DB_FILE="$CONFIG_DIR/subscribers.db"
VENV="${VENV:-$HOME/klipper-telegram-env}"
SERVICE='klipper-telegram.service'
UNIT_FILE="/etc/systemd/system/$SERVICE"
ASVC_FILE="$PRINTER_DATA/moonraker.asvc"
STEPS=6

info() { printf '  [INFO] %s\n' "$*"; }
ok() { printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*" >&2; }
section() { printf '\n============================================================\n[%s/%s] %s\n============================================================\n' "$1" "$STEPS" "$2"; }
ask() {
  local answer
  if [[ ! -t 0 ]]; then
    warn "Noninteractive session: defaulting to No: $1"
    return 1
  fi
  read -r -p "  $1 [y/N] " answer || answer=''
  [[ "$answer" =~ ^[Yy]$ ]]
}
backup() {
  local source="$1" stamp target
  stamp="$(date +%Y%m%d-%H%M%S)"
  target="${source}.uninstall-${stamp}.bak"
  cp -p -- "$source" "$target"
  info "Backup: $target"
}
# Remove only the exact section header and its body, without touching subsequent sections.
remove_ini_section() {
  local file="$1" name="$2"
  [[ -f "$file" ]] || { info "Not found: $file"; return 0; }
  if ! grep -Fxq "[$name]" "$file"; then
    info "Section [$name] not present"
    return 0
  fi
  backup "$file"
  python3 - "$file" "$name" <<'PY'
from pathlib import Path
import re
import sys
p = Path(sys.argv[1]); name = sys.argv[2]
text = p.read_text(encoding="utf-8")
header = re.compile(r"^\s*\[([^\]\r\n]+)\]\s*$")
lines = text.splitlines(keepends=True)
output = []
in_target = False
for line in lines:
    match = header.fullmatch(line.rstrip("\r\n"))
    if match:
        in_target = match.group(1) == name
    if not in_target:
        output.append(line)
p.write_text("".join(output), encoding="utf-8")
PY
  ok "Removed section [$name] from $file"
}

printf '\nKlipper Telegram Bot - interactive uninstaller\n'
info "User: $(id -un)"
info "Repository: $ROOT"
info "Printer data: $PRINTER_DATA"
warn 'Personal configuration, token, and subscriber database are preserved unless explicitly deleted.'

section 1 'Checking prerequisites and installation paths'
for cmd in systemctl sudo python3; do
  command -v "$cmd" >/dev/null 2>&1 || { warn "Required command not found: $cmd"; exit 1; }
done
ok 'Required commands available'

section 2 'Stopping and removing the systemd service'
if systemctl list-unit-files "$SERVICE" --no-legend 2>/dev/null | grep -Fq "$SERVICE" || [[ -f "$UNIT_FILE" ]]; then
  info "Stopping $SERVICE"
  sudo systemctl stop "$SERVICE" || warn 'Service could not be stopped or was already stopped'
  info "Disabling $SERVICE"
  sudo systemctl disable "$SERVICE" || warn 'Service could not be disabled or was already disabled'
else
  info 'Service is not registered'
fi
if [[ -f "$UNIT_FILE" ]]; then
  sudo rm -f -- "$UNIT_FILE"
  ok "Removed $UNIT_FILE"
else
  info 'No systemd unit file to remove'
fi
sudo systemctl daemon-reload
sudo systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
ok 'systemd refreshed'

section 3 'Optional Klipper G-Code macros removal'
if ask 'Remove telegram_macros.cfg and its printer.cfg include?'; then
  if [[ -f "$PRINTER_CONF" ]] && grep -Eq '^[[:space:]]*\[include[[:space:]]+telegram_macros\.cfg\][[:space:]]*$' "$PRINTER_CONF"; then
    backup "$PRINTER_CONF"
    python3 - "$PRINTER_CONF" <<'PY'
from pathlib import Path
import re, sys
p = Path(sys.argv[1])
lines = p.read_text(encoding='utf-8').splitlines(keepends=True)
rx = re.compile(r'^\s*\[include\s+telegram_macros\.cfg\]\s*$')
p.write_text(''.join(x for x in lines if not rx.fullmatch(x.rstrip('\r\n'))), encoding='utf-8')
PY
    ok 'Removed [include telegram_macros.cfg] from printer.cfg'
  fi
  if [[ -f "$MACROS_FILE" ]]; then
    backup "$MACROS_FILE"
    rm -- "$MACROS_FILE"
    ok 'Removed telegram_macros.cfg'
  else
    info 'No macro file found'
  fi
  info 'Klipper RESTART required if the include was changed'
else
  info 'Klipper macros preserved'
fi

section 4 'Optional Moonraker Update Manager removal'
if ask 'Remove [update_manager klipper-telegram] and service authorization?'; then
  remove_ini_section "$MOONRAKER_CONF" 'update_manager klipper-telegram'
  if [[ -f "$ASVC_FILE" ]] && grep -Fxq 'klipper-telegram' "$ASVC_FILE"; then
    backup "$ASVC_FILE"
    python3 - "$ASVC_FILE" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(''.join(line for line in p.read_text(encoding='utf-8').splitlines(keepends=True)
                     if line.strip() != 'klipper-telegram'), encoding='utf-8')
PY
    ok 'Removed service authorization from moonraker.asvc'
  fi
  info 'Moonraker restart required to load changes'
else
  warn 'Update Manager entry retained; it may reference the removed service'
fi

section 5 'Optional environment and private data cleanup'
if [[ -d "$VENV" ]]; then
  if ask "Remove dedicated Python environment ($VENV)?"; then
    case "$VENV" in
      "$HOME"|/|"$PRINTER_DATA"|"$CONFIG_DIR") warn 'Unsafe venv path; refusing to delete' ;;
      *) rm -rf -- "$VENV"; ok "Removed $VENV" ;;
    esac
  else
    info "Python environment preserved: $VENV"
  fi
else
  info 'No Python environment found'
fi
info 'Sensitive files are retained by default.'
if ask 'PERMANENTLY delete Telegram config, token, and subscriber SQLite files?'; then
  if ask 'Confirm deletion of Telegram credentials and subscriber data?'; then
    for file in "$CONFIG_FILE" "$TOKEN_FILE" "$DB_FILE" "${DB_FILE}-wal" "${DB_FILE}-shm"; do
      if [[ -f "$file" ]]; then
        rm -- "$file"
        ok "Removed $file"
      fi
    done
  else
    info 'Private data deletion cancelled'
  fi
else
  info 'Telegram configuration, token and subscriber data preserved'
fi

section 6 'Summary'
ok 'Uninstallation steps completed'
info 'The Git repository was not deleted'
info 'Review warnings above for optional integrations left in place'
info 'Restart Moonraker if its configuration was modified'
info 'Run RESTART in Mainsail if Klipper macro includes were modified'
