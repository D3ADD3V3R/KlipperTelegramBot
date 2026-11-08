#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then echo 'Run make install as the Klipper user, NOT root.' >&2; exit 1; fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRINTER_DATA="${PRINTER_DATA:-$HOME/printer_data}"
CONFIG_DIR="$PRINTER_DATA/config"
CONFIG_FILE="$CONFIG_DIR/klipper_telegram.conf"
TOKEN_FILE="$CONFIG_DIR/klipper_telegram.token"
MOONRAKER_CONF="$CONFIG_DIR/moonraker.conf"
KLIPPER_CONF="$CONFIG_DIR/printer.cfg"
MACROS_FILE="$CONFIG_DIR/telegram_macros.cfg"
VENV="${VENV:-$HOME/klipper-telegram-env}"
SERVICE='klipper-telegram.service'

ask() {
 local ans
 read -r -p "$1 [Y/n] " ans || true
 [[ ! "$ans" =~ ^[Nn]$ ]]
}

mkdir -p "$CONFIG_DIR"
echo 'Klipper Telegram - installer'
echo "Repository: $ROOT"
echo "User: $(id -un)"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"

if [[ ! -e "$CONFIG_FILE" ]] && ask 'Create default configuration?'; then
  cp "$ROOT/examples/klipper_telegram.conf" "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
  echo "Config: $CONFIG_FILE"
else
  echo 'Existing configuration preserved (or opted out).'
fi

if [[ ! -e "$TOKEN_FILE" ]] && ask 'Configure a BotFather token now?'; then
  read -r -s -p 'Bot token (not echoed): ' BOT_TOKEN; echo
  if [[ -n "$BOT_TOKEN" ]]; then
    (umask 077; printf '%s\n' "$BOT_TOKEN" > "$TOKEN_FILE")
    echo "Token saved to $TOKEN_FILE"
  fi
  unset BOT_TOKEN
fi

if [[ ! -e "$MACROS_FILE" ]] && ask 'Install optional Klipper G-Code macros?'; then
  cp "$ROOT/examples/telegram_macros.cfg" "$MACROS_FILE"
  if [[ -f "$KLIPPER_CONF" ]] && ! grep -Fqx '[include telegram_macros.cfg]' "$KLIPPER_CONF"; then
    if ask 'Add include to printer.cfg?'; then
      printf '\n[include telegram_macros.cfg]\n' >> "$KLIPPER_CONF"
    fi
  fi
  echo 'Note: Restart Klipper to load installed macros.'
fi

UNIT="$(mktemp)"
cat > "$UNIT" <<EOF
[Unit]
Description=Klipper Telegram notification agent
After=network-online.target moonraker.service
Wants=network-online.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT
ExecStart=$VENV/bin/python -m klipper_telegram --config $CONFIG_FILE serve
Restart=always
RestartSec=5
UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF
sudo install -m 644 "$UNIT" "/etc/systemd/system/$SERVICE"
rm -f "$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
if [[ -f "$CONFIG_FILE" ]]; then
  sudo systemctl restart "$SERVICE"
else
  echo 'No configuration found: service is registered but not started.'
fi

if ask 'Configure Moonraker Update Manager?'; then
  if [[ ! -d "$ROOT/.git" ]]; then
    echo 'Not a git clone: skipping update manager (git_repo needs a git checkout).'
  elif [[ ! -f "$MOONRAKER_CONF" ]]; then
    echo 'moonraker.conf missing; update manager not configured.'
  elif grep -Eq '^\[update_manager klipper-telegram\]' "$MOONRAKER_CONF"; then
    echo 'Existing update manager section preserved.'
  else
    ORIGIN="$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)"
    if [[ "$ORIGIN" =~ ^https://gitlab\.com/[^[:space:]]+\.git$ || "$ORIGIN" =~ ^https://gitlab\.com/[^[:space:]]+$ ]]; then
      cat >> "$MOONRAKER_CONF" <<EOF

[update_manager klipper-telegram]
type: git_repo
path: $ROOT
origin: $ORIGIN
primary_branch: main
virtualenv: $VENV
requirements: requirements.txt
managed_services: klipper-telegram
EOF
      ASVC="$PRINTER_DATA/moonraker.asvc"
      touch "$ASVC"
      grep -Fxq 'klipper-telegram' "$ASVC" || echo 'klipper-telegram' >> "$ASVC"
      echo "Update manager configured. Restart Moonraker to activate."
    else
      echo 'Update manager skipped: set a real public HTTPS GitLab origin and rerun installer.'
    fi
  fi
fi

echo 'Completed. Check: systemctl status klipper-telegram'
echo 'Logs: journalctl -u klipper-telegram -f'
