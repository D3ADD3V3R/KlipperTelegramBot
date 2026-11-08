"""Persistent pairing, configuration, and event template helpers."""
import configparser

ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def load_config(path):
    cfg = configparser.ConfigParser(interpolation=None)
    if not cfg.read(path, encoding='utf-8'):
        raise FileNotFoundError(path)
    return cfg


def render(template, values):
    # Only known placeholders are replaced; no arbitrary Python expressions.
    import re
    return re.sub(r'\{(filename|printer|state|progress|error|user|code|bot|camera_name)\}',
                  lambda m: str(values.get(m.group(1), '')), template)


