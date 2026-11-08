import asyncio
import unittest
from klipper_telegram.core import render
from klipper_telegram.store import MoonrakerStore


class MemoryStore(MoonrakerStore):
    def __init__(self):
        super().__init__('http://localhost:7125')
        self.data = {}

    async def get(self, key, default=None):
        return self.data.get(key, default)

    async def put(self, key, value):
        self.data[key] = value

    async def delete(self, key):
        return self.data.pop(key, None)


class PairingTests(unittest.IsolatedAsyncioTestCase):
    async def test_pair_once(self):
        store = MemoryStore()
        code, _ = await store.generate(600)
        self.assertTrue((await store.claim(code, 42, 42, 'test'))[0])
        self.assertFalse((await store.claim(code, 43, 43, 'test2'))[0])
        self.assertTrue(await store.is_subscribed(42, 42))
        self.assertFalse(await store.is_subscribed(42, 99))
        self.assertTrue(await store.unsubscribe(42))
        self.assertFalse(await store.is_subscribed(42, 42))

    async def test_replacement_and_expiration(self):
        store = MemoryStore()
        old, _ = await store.generate(600)
        new, _ = await store.generate(600)
        self.assertFalse((await store.claim(old, 1, 1, 'a'))[0])
        self.assertTrue((await store.claim(new, 2, 2, 'b'))[0])
        third, _ = await store.generate(600)
        store.data['pairing']['expires'] = 0
        self.assertEqual(await store.expire(), 1)
        self.assertEqual(await store.expire(), 0)
        self.assertFalse((await store.claim(third, 3, 3, 'c'))[0])

    async def test_identity_persistent(self):
        store = MemoryStore()
        self.assertEqual(await store.identity(), await store.identity())

    async def test_template_whitelist(self):
        self.assertEqual(render('Hello {user} {unknown}', {'user': '@test'}),
                         'Hello @test {unknown}')


if __name__ == '__main__':
    unittest.main()
