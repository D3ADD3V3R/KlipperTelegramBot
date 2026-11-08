"""Moonraker namespace persistence. No plugin-owned SQLite database.

Moonraker already owns SQLite and exposes a documented REST/JSON-RPC API.
This adapter uses REST rather than a second WebSocket (the agent connection
needs to receive server-initiated remote methods).
"""
import asyncio
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from pathlib import Path

import aiohttp

from .core import ALPHABET

LOG = logging.getLogger('klipper-telegram')
NAMESPACE = 'klipper_telegram'


class MoonrakerStore:
    def __init__(self, url, api_key='', session=None):
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.session = session
        self._own_session = False
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
            self._own_session = True
        return self

    async def __aexit__(self, *_):
        if self._own_session:
            await self.session.close()

    async def _request(self, verb, key=None, value=None):
        params = {'namespace': NAMESPACE}
        if key is not None:
            params['key'] = key
        headers = {'X-Api-Key': self.api_key} if self.api_key else {}
        payload = dict(params)
        if verb == 'POST':
            payload['value'] = value
        async with self.session.request(
            verb, self.url + '/server/database/item',
            params=params if verb != 'POST' else None,
            json=payload if verb == 'POST' else None,
            headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            result = await response.json(content_type=None)
            if response.status >= 400 or 'error' in result:
                raise RuntimeError(f'Moonraker database {verb} failed (HTTP {response.status}): {result.get("error", "unknown")}')
            return result.get('result', result).get('value')

    async def get(self, key, default=None):
        try:
            return await self._request('GET', key)
        except RuntimeError as exc:
            # A missing key should not be confused with server outages.
            if 'HTTP 404' in str(exc) or '404' in str(exc) or 'not found' in str(exc).lower():
                return default
            raise

    async def put(self, key, value):
        return await self._request('POST', key, value)

    async def delete(self, key):
        try:
            return await self._request('DELETE', key)
        except RuntimeError as exc:
            if 'HTTP 404' in str(exc) or '404' in str(exc) or 'not found' in str(exc).lower():
                return None
            raise

    async def identity(self):
        async with self._lock:
            identity = await self.get('printer_id')
            if identity is None:
                identity = str(uuid.uuid4())
                await self.put('printer_id', identity)
            return identity

    async def arm_console(self, ttl=60):
        if not 1 <= ttl <= 300:
            raise ValueError('Arm duration must be 1-300 seconds')
        async with self._lock:
            await self.put('console_arm_until', time.time() + ttl)

    async def consume_console_arm(self):
        async with self._lock:
            armed = await self.get('console_arm_until', 0)
            await self.delete('console_arm_until')
            return bool(armed and float(armed) > time.time())

    async def generate(self, ttl=600):
        if not 60 <= ttl <= 3600:
            raise ValueError('Pairing lifetime must be between 60 and 3600 seconds')
        code = ''.join(secrets.choice(ALPHABET) for _ in range(8))
        code = code[:4] + '-' + code[4:]
        now = time.time()
        async with self._lock:
            await self.put('pairing', {
                'digest': hashlib.sha256(code.encode()).hexdigest(),
                'expires': now + ttl, 'state': 'pending', 'created': now,
                'attempts': {}, 'failures': 0,
            })
        return code, now + ttl

    async def expire(self):
        async with self._lock:
            p = await self.get('pairing')
            if p and p.get('state') == 'pending' and p['expires'] <= time.time():
                p['state'] = 'expired'
                await self.put('pairing', p)
                return 1
            return 0

    async def claim(self, code, user_id, chat_id, username, max_attempts=5):
        if not isinstance(code, str):
            return False, 'invalid'
        code = code.strip().upper()
        now = time.time()
        async with self._lock:
            p = await self.get('pairing')
            if not p or p.get('state') != 'pending' or p['expires'] <= now:
                return False, 'expired'
            attempts = p.setdefault('attempts', {})
            key = str(user_id)
            record = attempts.get(key, {'count': 0, 'first': now})
            if now - record['first'] >= 600:
                record = {'count': 0, 'first': now}
            if record['count'] >= max_attempts or p.get('failures', 0) >= 30:
                return False, 'rate_limited'
            record['count'] += 1
            attempts[key] = record
            if not hmac.compare_digest(p['digest'], hashlib.sha256(code.encode()).hexdigest()):
                p['failures'] = p.get('failures', 0) + 1
                await self.put('pairing', p)
                return False, 'invalid'
            # Commit subscription before consuming the pairing code; retry on storage errors.
            subscribers = await self.get('subscribers', {}) or {}
            subscribers[key] = {'user_id': user_id, 'chat_id': chat_id,
                                'username': username, 'subscribed': now, 'enabled': True}
            await self.put('subscribers', subscribers)
            p.update({'state': 'used', 'user_id': user_id, 'username': username})
            await self.put('pairing', p)
            return True, 'ok'

    async def subscribers(self):
        records = await self.get('subscribers', {}) or {}
        return [entry for entry in records.values() if entry.get('enabled')]

    async def is_subscribed(self, user_id, chat_id):
        records = await self.get('subscribers', {}) or {}
        record = records.get(str(user_id), {})
        return record.get('chat_id') == chat_id and bool(record.get('enabled'))

    async def unsubscribe(self, user_id):
        async with self._lock:
            records = await self.get('subscribers', {}) or {}
            record = records.get(str(user_id))
            if not record or not record.get('enabled'):
                return False
            record['enabled'] = False
            await self.put('subscribers', records)
            return True

    async def migrate_legacy(self, file_path):
        """Import old subscribers/identity once, retaining the original DB untouched.

        Only import if no subscribers exist in Moonraker yet. The marker prevents
        repeat imports after a deliberate unsubscribe-all action.
        """
        import sqlite3
        path = Path(file_path)
        if not path.is_file():
            return False
        async with self._lock:
            if await self.get('legacy_migrated', False):
                return False
            # Read from a SQLite read-only connection; never modify the source.
            with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as conn:
                conn.row_factory = sqlite3.Row
                users = [dict(row) for row in conn.execute('SELECT * FROM subscribers')]
                row = conn.execute("SELECT value FROM metadata WHERE key='printer_id'").fetchone()
                old_id = row['value'] if row else None
            existing = await self.get('subscribers', {}) or {}
            for user in users:
                existing.setdefault(str(user['user_id']), {
                    'user_id': user['user_id'], 'chat_id': user['chat_id'],
                    'username': user['username'], 'subscribed': user['subscribed'],
                    'enabled': bool(user['enabled'])})
            if users:
                await self.put('subscribers', existing)
            if old_id and await self.get('printer_id') is None:
                await self.put('printer_id', old_id)
            await self.put('legacy_migrated', True)
            LOG.info('Imported %d legacy subscriber records; original SQLite retained', len(users))
            return True
