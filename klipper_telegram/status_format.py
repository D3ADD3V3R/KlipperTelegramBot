"""Formatting for the interactive Telegram /status response only."""
import html
import math


def number(value):
    """Accept finite numeric Moonraker values but never invent missing data."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def duration(seconds):
    value = number(seconds)
    if value is None:
        return '–'
    total = int(value)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f'{hours} h {minutes:02d} min'
    if minutes:
        return f'{minutes} min {seconds:02d} s'
    return f'{seconds} s'


def row(label, value):
    return f'<b>{html.escape(label)}:</b> {html.escape(str(value))}'


def status_message(printer, stats, display=None, vsd=None, estimated_time=None):
    """Return an HTML-formatted Telegram /status message.

    `estimated_time` is Moonraker file metadata, not a native Klipper ETA.
    Remaining time uses estimated_time * (1 - progress), following Moonraker's
    documented approach. Missing inputs result in an explicit unknown value.
    """
    stats = stats if isinstance(stats, dict) else {}
    display = display if isinstance(display, dict) else {}
    vsd = vsd if isinstance(vsd, dict) else {}
    state = stats.get('state') or 'unknown'
    filename = stats.get('filename') or '–'
    heading = html.escape(str(printer))
    if state not in ('printing', 'complete'):
        return f'{heading}: {html.escape(str(state))}\n{html.escape(str(stats.get("filename") or ""))}'

    if state == 'printing':
        progress = number(display.get('progress'))
        if progress is None:
            progress = number(vsd.get('progress'))
        if progress is not None and progress > 1:
            progress = None
        progress_str = f'{progress * 100:.1f} %'.replace('.', ',') if progress is not None else '–'
        info = stats.get('info') or {}
        if not isinstance(info, dict):
            info = {}
        current, total = number(info.get('current_layer')), number(info.get('total_layer'))
        layer = f'{int(current)} / {int(total)}' if current is not None and total is not None and total > 0 else '–'
        estimated = number(estimated_time)
        remaining = duration(max(0, estimated * (1 - progress))) if estimated is not None and progress is not None else '–'
        lines = [f'🖨️ <b>{heading} – Druck läuft</b>', '',
                 row('Dateiname', filename), row('Aktuelle Dauer', duration(stats.get('print_duration'))),
                 row('Verbleibende Zeit (Schätzung)', remaining), row('Layer', layer),
                 row('Fortschritt', progress_str)]
    else:
        filament = number(stats.get('filament_used'))
        filament_text = f'{filament / 1000:.2f} m'.replace('.', ',') if filament is not None else '–'
        lines = [f'✅ <b>{heading} – Druck abgeschlossen</b>', '',
                 row('Dateiname', filename),
                 row('Gesamtdauer', duration(stats.get('total_duration') if stats.get('total_duration') is not None else stats.get('print_duration'))),
                 row('Filamentverbrauch', filament_text)]
    return '\n'.join(lines)
