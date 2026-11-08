import unittest
from unittest.mock import patch
from test_core import MemoryStore


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_arm_single_use(self):
        store = MemoryStore()
        self.assertFalse(await store.consume_console_arm())
        await store.arm_console(60)
        self.assertTrue(await store.consume_console_arm())
        self.assertFalse(await store.consume_console_arm())

    async def test_expired_arm(self):
        store = MemoryStore()
        with patch('klipper_telegram.store.time.time', return_value=100):
            await store.arm_console(10)
        with patch('klipper_telegram.store.time.time', return_value=111):
            self.assertFalse(await store.consume_console_arm())

    async def test_arm_duration_validation(self):
        store = MemoryStore()
        for seconds in (0, -1, 301):
            with self.assertRaises(ValueError):
                await store.arm_console(seconds)

    async def test_global_attempt_budget(self):
        store = MemoryStore()
        code, _ = await store.generate()
        for i in range(30):
            self.assertFalse((await store.claim('BAD-CODE', 100+i, 100+i, 'bot'))[0])
        self.assertEqual((await store.claim(code, 999, 999, 'valid'))[1], 'rate_limited')
        new, _ = await store.generate()
        self.assertTrue((await store.claim(new, 999, 999, 'valid'))[0])
