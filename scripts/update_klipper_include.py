#!/usr/bin/env python3
"""Safely manage the Klipper Telegram include without corrupting SAVE_CONFIG.

The generated SAVE_CONFIG block is preserved byte-for-byte, except that an
incorrectly appended telegram include is removed from its tail.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import tempfile

MARKER = b'#*# <---------------------- SAVE_CONFIG ---------------------->'
INCLUDE = b'[include telegram_macros.cfg]'
INCLUDE_RE = re.compile(rb'^[ \t]*\[include[ \t]+telegram_macros\.cfg\][ \t]*(?:\r?\n|$)', re.I | re.M)


def update(path: Path, mode: str) -> str:
    original = path.read_bytes()
    marker_pos = original.find(MARKER)
    head = original if marker_pos == -1 else original[:marker_pos]
    tail = b'' if marker_pos == -1 else original[marker_pos:]
    occurrences = len(INCLUDE_RE.findall(original))
    if mode == 'ensure':
        # Clean up duplicates, including old includes added after SAVE_CONFIG.
        head = INCLUDE_RE.sub(b'', head)
        tail = INCLUDE_RE.sub(b'', tail)
        newline = b'\r\n' if b'\r\n' in original else b'\n'
        # Insert immediately before SAVE_CONFIG or append to a config without it.
        head = head.rstrip(b'\r\n') + newline + INCLUDE + newline + (newline if tail else b'')
    elif mode == 'remove':
        head = INCLUDE_RE.sub(b'', head)
        tail = INCLUDE_RE.sub(b'', tail)
    else:
        raise ValueError(mode)
    updated = head + tail
    if updated == original:
        return 'unchanged'
    backup = path.with_name(path.name + '.telegram-install.bak')
    if not backup.exists():
        shutil.copy2(path, backup)
    fd, tmp_name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        shutil.copymode(path, tmp_name)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return 'moved' if mode == 'ensure' and occurrences else ('added' if mode == 'ensure' else 'removed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['ensure', 'remove'])
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    print(update(args.path, args.action))
