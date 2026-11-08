#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  printf 'ERROR: Run make install as your normal Klipper user, not with sudo.\n' >&2
  exit 1
fi

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
STEPS=7
SERVICE_STATE='not installed'
UPDATE_STATE='not configured'
MACRO_STATE='not configured'
CLI_STATE='not configured'
CONFIG_STATE='not created'
TOKEN_STATE='not configured'
RESTART_KLIPPER=false
RESTART_MOONRAKER=false
RESTART_TELEGRAM=false

info()    { printf '  [INFO] %s\n' "$*"; }
ok()      { printf '  [ OK ] %s\n' "$*"; }
warn()    { printf '  [WARN] %s\n' "$*" >&2; }
section() { printf '\n============================================================\n[%s/%s] %s\n============================================================\n' "$1" "$STEPS" "$2"; }
ask() {
  local ans
  if [[ ! -t 0 ]]; then
    warn "No interactive terminal: defaulting to No for: $1"
    return 1
  fi
  read -r -p "  $1 [Y/n] " ans || ans=n
  [[ ! "$ans" =~ ^[Nn]$ ]]
}

trap 'warn "Installation stopped at line $LINENO (exit code $?). See the output above."' ERR
printf '\nKlipper Telegram Bot - interactive installer\n'
info "User: $(id -un) (uid=$(id -u))"
info "Repository: $ROOT"
info "Config location: $CONFIG_DIR"
info "Python environment: $VENV"
info 'Existing configuration and token will be preserved; legacy subscribers.db is import-only.'

section 1 'Checking prerequisites and installation paths'
for bin in python3 sudo systemctl git; do
  if command -v "$bin" >/dev/null 2>&1; then
    ok "$bin: $(command -v "$bin")"
  else
    warn "Required command not found: $bin"
    exit 1
  fi
done
[[ -f "$ROOT/requirements.txt" ]] || { warn 'requirements.txt not found'; exit 1; }
[[ -f "$ROOT/examples/klipper_telegram.conf" ]] || { warn 'Example config not found'; exit 1; }
mkdir -p "$CONFIG_DIR"
ok "Configuration directory ready: $CONFIG_DIR"

section 2 'Creating Python environment and installing dependencies'
if [[ -x "$VENV/bin/python" ]]; then
  info 'Existing virtual environment found; updating packages.'
else
  info 'Creating isolated Python virtual environment.'
  python3 -m venv "$VENV" || { warn 'Could not create venv; install python3-venv and retry'; exit 1; }
fi
info 'Upgrading pip (package installation output follows).'
"$VENV/bin/python" -m pip install --upgrade pip
info 'Installing application dependencies (package installation output follows).'
"$VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"
info 'Installing klipper-telegram package and CLI entry point into virtual environment.'
"$VENV/bin/python" -m pip install -e "$ROOT"
if [[ ! -x "$VENV/bin/klipper-telegram" ]]; then
  warn 'CLI installation failed: virtualenv executable missing.'
  exit 1
fi
ok "CLI installed: $VENV/bin/klipper-telegram"
CLI_STATE="$VENV/bin/klipper-telegram"
GLOBAL_CLI='/usr/local/bin/klipper-telegram'
if [[ -L "$GLOBAL_CLI" ]]; then
  if [[ "$(readlink -f "$GLOBAL_CLI")" == "$(readlink -f "$VENV/bin/klipper-telegram")" ]]; then
    ok 'Existing global CLI symlink is correct.'
  else
    warn "Global command $GLOBAL_CLI belongs to another installation; leaving unchanged."
  fi
elif [[ -e "$GLOBAL_CLI" ]]; then
  warn "Global command $GLOBAL_CLI already exists; leaving unchanged."
else
  info "Registering global CLI symlink: $GLOBAL_CLI"
  sudo ln -s "$VENV/bin/klipper-telegram" "$GLOBAL_CLI"
  ok 'Global klipper-telegram command registered.'
fi
ok 'Python environment and dependencies ready.' 

section 3 'Preparing local configuration and Telegram credentials'
if [[ -f "$CONFIG_FILE" ]]; then
  ok "Existing configuration preserved: $CONFIG_FILE"
  CONFIG_STATE='existing file retained'
elif ask 'Create example klipper_telegram.conf?'; then
  (umask 077; cp "$ROOT/examples/klipper_telegram.conf" "$CONFIG_FILE")
  chmod 600 "$CONFIG_FILE"
  ok "Created: $CONFIG_FILE (permissions 600)"
  CONFIG_STATE='created'
else
  warn 'Configuration not created. Service will be registered but cannot run until configured.'
fi
if [[ -e "$TOKEN_FILE" ]]; then
  ok 'Existing Telegram token file preserved (token not displayed).'
  TOKEN_STATE='existing file retained'
elif ask 'Enter a BotFather token now?'; then
  read -r -s -p '  Bot token (hidden): ' BOT_TOKEN || BOT_TOKEN=''
  printf '\n'
  if [[ -n "$BOT_TOKEN" ]]; then
    (umask 077; printf '%s\n' "$BOT_TOKEN" > "$TOKEN_FILE")
    chmod 600 "$TOKEN_FILE"
    ok "Token stored at $TOKEN_FILE (permissions 600)"
    TOKEN_STATE='created'
  else
    warn 'Empty token: skipped.'
  fi
  unset BOT_TOKEN
else
  info 'Token entry skipped. You can configure it later.'
fi

section 4 'Installing optional Klipper G-Code macros'
if [[ -f "$MACROS_FILE" ]]; then
  ok "Existing macros preserved: $MACROS_FILE"
  MACRO_STATE='existing file retained'
elif ask 'Install TELEGRAM_PAIRING_CREATE and TELEGRAM_STATUS macros?'; then
  cp "$ROOT/examples/telegram_macros.cfg" "$MACROS_FILE"
  RESTART_KLIPPER=true
  ok "Installed macros: $MACROS_FILE"
  MACRO_STATE='installed'
else
  info 'Macro installation skipped.'
fi
if [[ -f "$MACROS_FILE" && -f "$KLIPPER_CONF" ]]; then
  if grep -Eq '^[[:space:]]*\[include[[:space:]]+telegram_macros\.cfg\][[:space:]]*$' "$KLIPPER_CONF"; then
    info 'Checking existing include position (and repairing if necessary).'
    result="$(python3 "$ROOT/scripts/update_klipper_include.py" ensure "$KLIPPER_CONF")"
    ok "Klipper include verified: $result"
    [[ "$result" != unchanged ]] && RESTART_KLIPPER=true
  elif ask 'Add [include telegram_macros.cfg] to printer.cfg before SAVE_CONFIG?'; then
    result="$(python3 "$ROOT/scripts/update_klipper_include.py" ensure "$KLIPPER_CONF")"
    ok "Klipper include installed: $result; RESTART required."
    RESTART_KLIPPER=true
  else
    warn 'Macro file exists but is not included automatically. Add the include manually when ready.'
  fi
elif [[ -f "$MACROS_FILE" ]]; then
  warn "printer.cfg not found at $KLIPPER_CONF; add include manually."
fi

section 5 'Registering the systemd service'
UNIT="$(mktemp)"
trap 'rm -f "${UNIT:-}"' EXIT
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
RestartSec=20
UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF
info "Installing /etc/systemd/system/$SERVICE (sudo may ask for your password)."
sudo install -m 644 "$UNIT" "/etc/systemd/system/$SERVICE"
info 'Reloading systemd unit definitions.'
sudo systemctl daemon-reload
info 'Enabling service on boot.'
sudo systemctl enable "$SERVICE"
if [[ -f "$CONFIG_FILE" ]]; then
  info 'Service installed; start/restart deferred until final confirmation.'
  RESTART_TELEGRAM=true
  SERVICE_STATE='enabled; restart pending'
else
  warn 'Service enabled but not started because configuration does not exist.'
  SERVICE_STATE='enabled only'
fi

section 6 'Optional Moonraker Update Manager integration'
UPDATE_SECTION_PRESENT=false
if [[ -f "$MOONRAKER_CONF" ]] && grep -Eq '^[[:space:]]*\[update_manager klipper-telegram\][[:space:]]*$' "$MOONRAKER_CONF"; then
  UPDATE_SECTION_PRESENT=true
fi
if [[ "$UPDATE_SECTION_PRESENT" == true ]]; then
  info 'Found existing [update_manager klipper-telegram] entry.'
  UPDATE_STATE='already configured; verify Moonraker reload'
fi
if [[ "$UPDATE_SECTION_PRESENT" == true ]] || ask 'Configure Moonraker Update Manager?'; then
  if [[ ! -d "$ROOT/.git" ]]; then
    warn 'Update Manager requires a real Git checkout (.git directory). ZIP extraction is not suitable for git_repo.'
  elif [[ ! -f "$MOONRAKER_CONF" ]]; then
    warn "Moonraker configuration not found: $MOONRAKER_CONF"
  else
    ORIGIN="$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)"
    info "Git origin: ${ORIGIN:-none}"
    info "Git branch: $(git -C "$ROOT" branch --show-current 2>/dev/null || echo unknown)"
    if [[ "$UPDATE_SECTION_PRESENT" == true ]]; then
      ok 'Existing update_manager entry preserved (no duplicates).'
      info 'Confirm its path, origin and branch match this checkout:'
      sed -n '/^\[update_manager klipper-telegram\]/,/^\[/{ /^\[update_manager klipper-telegram\]/p; /^\[update_manager klipper-telegram\]/!{ /^\[/!p; }; }' "$MOONRAKER_CONF" || true
    else
      case "$ORIGIN" in
        https://github.com/D3ADD3V3R/KlipperTelegramBot|https://github.com/D3ADD3V3R/KlipperTelegramBot.git|git@github.com:D3ADD3V3R/KlipperTelegramBot.git)
          cp -p "$MOONRAKER_CONF" "${MOONRAKER_CONF}.klipper-telegram-install.bak"
          cat >> "$MOONRAKER_CONF" <<EOF

[update_manager klipper-telegram]
type: git_repo
path: $ROOT
origin: $ORIGIN
primary_branch: main
channel: dev
virtualenv: $VENV
requirements: requirements.txt
managed_services: klipper-telegram
EOF
          ok 'Update Manager configuration appended (backup created).'
          UPDATE_STATE='configured; restart Moonraker'
        RESTART_MOONRAKER=true
          RESTART_MOONRAKER=true
          ;;
        *) warn 'Git origin does not match expected public GitHub repository; skipping new Update Manager entry.' ;;
      esac
    fi
    if [[ "$UPDATE_SECTION_PRESENT" == true || "$UPDATE_STATE" == 'configured; restart Moonraker' ]]; then
      ASVC="$PRINTER_DATA/moonraker.asvc"
      if [[ ! -f "$ASVC" ]]; then
        touch "$ASVC"
      fi
      if ! grep -Fxq 'klipper-telegram' "$ASVC"; then
        cp -p "$ASVC" "${ASVC}.klipper-telegram-install.bak"
        printf '%s\n' 'klipper-telegram' >> "$ASVC"
        ok 'Added klipper-telegram to moonraker.asvc (backup created).'
        UPDATE_STATE='configured; restart Moonraker'
        RESTART_MOONRAKER=true
      else
        ok 'Service authorization in moonraker.asvc already present.'
      fi
      info 'Moonraker restart will be offered at installation end.'
      info 'If still absent, inspect: journalctl -u moonraker -n 80 --no-pager'
    fi
  fi
else
  info 'Moonraker Update Manager setup skipped.'
fi

section 7 'Installation summary'
printf '  Repository:        %s\n' "$ROOT"
printf '  Configuration:     %s\n' "$CONFIG_STATE"
printf '  Telegram token:    %s\n' "$TOKEN_STATE"
printf '  Klipper macros:    %s\n' "$MACRO_STATE"
printf '  Service:           %s\n' "$SERVICE_STATE"
printf '  CLI:               %s\n' "$CLI_STATE"
printf '  Update Manager:    %s\n' "$UPDATE_STATE"
printf '\nNext commands:\n'
printf '  systemctl status klipper-telegram --no-pager\n'
printf '  journalctl -u klipper-telegram -n 50 --no-pager\n'
printf '  klipper-telegram --help\n'
printf '  curl -fsS http://127.0.0.1:7125/machine/update/status\n'
if [[ "$UPDATE_STATE" == *'restart Moonraker'* || "$UPDATE_STATE" == 'already configured; verify Moonraker reload' ]]; then
  info 'Restart Moonraker to load the new Update Manager configuration.'
fi
if [[ "$MACRO_STATE" != 'not configured' ]]; then
  info 'Run RESTART in the Mainsail console after the macro include is in printer.cfg.'
fi
printf '\nAffected services: Klipper=%s Moonraker=%s Telegram=%s\n' "$RESTART_KLIPPER" "$RESTART_MOONRAKER" "$RESTART_TELEGRAM"
if [[ "$RESTART_KLIPPER" == true || "$RESTART_MOONRAKER" == true || "$RESTART_TELEGRAM" == true ]]; then
  # A running/paused print is never interrupted by installer restarts.
  PRINT_STATE="$(curl -fsS --max-time 5 http://127.0.0.1:7125/printer/objects/query?print_stats 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("result",{}).get("status",{}).get("print_stats",{}).get("state","unknown"))' 2>/dev/null || echo unknown)"
  if [[ "$PRINT_STATE" == printing || "$PRINT_STATE" == paused || "$PRINT_STATE" == unknown ]]; then
    warn "Not restarting services: printer state is $PRINT_STATE; restart manually after checking the printer."
  elif ask 'Restart affected services now?'; then
    if [[ "$RESTART_MOONRAKER" == true ]]; then
      sudo systemctl restart moonraker && ok 'Moonraker restarted'
    fi
    if [[ "$RESTART_KLIPPER" == true ]]; then
      sudo systemctl restart klipper && ok 'Klipper restarted'
    fi
    if [[ "$RESTART_TELEGRAM" == true ]]; then
      sudo systemctl restart klipper-telegram && ok 'Telegram service restarted'
    fi
  else
    warn 'Service restarts deferred by user.'
  fi
fi
ok 'Installer finished. Verify service and Telegram credentials before use.' 
