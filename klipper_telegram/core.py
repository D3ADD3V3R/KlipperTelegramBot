"""Persistent pairing, configuration, and event template helpers."""
import configparser
import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def load_config(path):
    cfg = configparser.ConfigParser(interpolation=None)
    if not cfg.read(path, encoding='utf-8'):
        raise FileNotFoundError(path)
    return cfg


def render(template, values):
    # Only known placeholders are replaced; no arbitrary Python expressions.
    import re
    return re.sub(r'\{(filename|printer|state|progress|error|user|code|bot)\}',
                  lambda m: str(values.get(m.group(1), '')), template)


class Store:
    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS pairings(
            id INTEGER PRIMARY KEY, digest TEXT NOT NULL, expires REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL,
            user_id INTEGER, username TEXT);
          CREATE TABLE IF NOT EXISTS subscribers(
            user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
            username TEXT, subscribed REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
          CREATE TABLE IF NOT EXISTS attempts(user_id INTEGER PRIMARY KEY, count INTEGER, first REAL);
        ''')
        if not self.db.execute("SELECT 1 FROM metadata WHERE key='printer_id'").fetchone():
            self.db.execute('INSERT INTO metadata VALUES (?,?)', ('printer_id', str(uuid.uuid4())))
        self.db.commit()

    def identity(self):
        return self.db.execute("SELECT value FROM metadata WHERE key='printer_id'").fetchone()[0]

    def arm_console(self, ttl=60):
        """Permit exactly one console-triggered pairing request for a short period."""
        if not 1 <= ttl <= 300:
            raise ValueError('Arm duration must be 1-300 seconds')
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('console_arm_until', ?)",
                            (str(time.time() + ttl),))

    def consume_console_arm(self):
        """Atomically consume a short-lived authorization; deny by default."""
        with self.db:
            row = self.db.execute("SELECT value FROM metadata WHERE key='console_arm_until'").fetchone()
            self.db.execute("DELETE FROM metadata WHERE key='console_arm_until'")
            return bool(row and float(row['value']) > time.time())

    def generate(self, ttl=600):
        code = ''.join(secrets.choice(ALPHABET) for _ in range(8))
        code = code[:4] + '-' + code[4:]
        now = time.time()
        with self.db:
            self.db.execute("UPDATE pairings SET state='replaced' WHERE state='pending'")
            self.db.execute("DELETE FROM metadata WHERE key='pairing_failures'")
            self.db.execute('INSERT INTO pairings(digest,expires,state,created) VALUES (?,?,?,?)',
                            (hashlib.sha256(code.encode()).hexdigest(), now+ttl, 'pending', now))
        return code, now+ttl

    def current(self):
        return self.db.execute("SELECT * FROM pairings WHERE state='pending' ORDER BY id DESC LIMIT 1").fetchone()

    def expire(self):
        now = time.time()
        rows = self.db.execute("SELECT id FROM pairings WHERE state='pending' AND expires<=?", (now,)).fetchall()
        with self.db:
            self.db.execute("UPDATE pairings SET state='expired' WHERE state='pending' AND expires<=?", (now,))
        return len(rows)

    def claim(self, code, user_id, chat_id, username, max_attempts=5):
        now = time.time()
        if not isinstance(code, str):
            return False, 'invalid'
        code = code.strip().upper()
        with self.db:
            attempt = self.db.execute('SELECT count,first FROM attempts WHERE user_id=?', (user_id,)).fetchone()
            if attempt and now-attempt['first'] < 600 and attempt['count'] >= max_attempts:
                return False, 'rate_limited'
            if not attempt or now-attempt['first'] >= 600:
                self.db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?)', (user_id,1,now))
            else:
                self.db.execute('UPDATE attempts SET count=count+1 WHERE user_id=?',(user_id,))
            p = self.current()
            # A shared attempt budget prevents unlimited guesses from rotating Telegram accounts.
            if p:
                count = self.db.execute("SELECT value FROM metadata WHERE key='pairing_failures'").fetchone()
                if count and int(count['value']) >= 30:
                    return False, 'rate_limited'
            if not p or p['expires'] <= now:
                return False, 'expired'
            if not hmac.compare_digest(p['digest'], hashlib.sha256(code.encode()).hexdigest()):
                self.db.execute("INSERT INTO metadata(key,value) VALUES('pairing_failures','1') "
                                "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
                return False, 'invalid'
            changed = self.db.execute("UPDATE pairings SET state='used', user_id=?, username=? WHERE id=? AND state='pending' AND expires>?",
                                      (user_id,username,p['id'],now)).rowcount
            if not changed:
                return False, 'expired'
            self.db.execute('INSERT INTO subscribers(user_id,chat_id,username,subscribed,enabled) VALUES (?,?,?,?,1) '
                            'ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, username=excluded.username, enabled=1',
                            (user_id,chat_id,username,now))
            self.db.execute('DELETE FROM attempts WHERE user_id=?',(user_id,))
        return True, 'ok'

    def subscribers(self):
        return self.db.execute('SELECT * FROM subscribers WHERE enabled=1').fetchall()

    def is_subscribed(self, user_id, chat_id):
        return bool(self.db.execute('SELECT 1 FROM subscribers WHERE user_id=? AND chat_id=? AND enabled=1',(user_id,chat_id)).fetchone())

    def unsubscribe(self,user_id):
        with self.db:
            return self.db.execute('UPDATE subscribers SET enabled=0 WHERE user_id=?',(user_id,)).rowcount > 0
