"""Exercise actual HTTP request and JSON shape, not just a fake key/value store."""
import tempfile
import sqlite3
import unittest
from pathlib import Path

from aiohttp import web

from klipper_telegram.store import MoonrakerStore


class DatabaseAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.records = {}

        async def item(request):
            if request.method == 'POST':
                params = await request.json()
            else:
                params = request.query
            if params.get('namespace') != 'klipper_telegram':
                return web.json_response({'error': {'message': 'bad namespace'}}, status=400)
            key = params.get('key')
            if request.method == 'GET':
                if key not in self.records:
                    return web.json_response({'error': {'code': 404, 'message': 'Not found'}}, status=404)
                value = self.records[key]
            elif request.method == 'POST':
                value = params['value']
                self.records[key] = value
            elif request.method == 'DELETE':
                if key not in self.records:
                    return web.json_response({'error': {'code': 404, 'message': 'Not found'}}, status=404)
                value = self.records.pop(key)
            return web.json_response({'result': {'namespace': 'klipper_telegram', 'key': key, 'value': value}})

        app = web.Application()
        app.router.add_route('*', '/server/database/item', item)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, host='127.0.0.1', port=0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.store = await MoonrakerStore(f'http://127.0.0.1:{port}').__aenter__()

    async def asyncTearDown(self):
        await self.store.__aexit__(None, None, None)
        await self.runner.cleanup()

    async def test_read_write_delete(self):
        self.assertIsNone(await self.store.get('unknown'))
        await self.store.put('subscribers', {'42': {'enabled': True}})
        self.assertEqual((await self.store.get('subscribers'))['42']['enabled'], True)
        await self.store.delete('subscribers')
        self.assertIsNone(await self.store.get('subscribers'))

    async def test_migrate_legacy_preserves_original(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'subscribers.db'
            con = sqlite3.connect(path)
            con.executescript('''CREATE TABLE subscribers(user_id INTEGER, chat_id INTEGER,
                username TEXT, subscribed REAL, enabled INTEGER);
                CREATE TABLE metadata(key TEXT, value TEXT);''')
            con.execute('INSERT INTO subscribers VALUES (42, 42, ?, 1, 1)', ('person',))
            con.execute('INSERT INTO metadata VALUES (?, ?)', ('printer_id', 'old-printer-id'))
            con.commit()
            con.close()
            self.assertTrue(await self.store.migrate_legacy(path))
            self.assertFalse(await self.store.migrate_legacy(path))
            self.assertEqual((await self.store.subscribers())[0]['username'], 'person')
            self.assertEqual(await self.store.identity(), 'old-printer-id')
            self.assertTrue(path.exists())
