import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from klipper_telegram.core import Store

class SecurityTests(unittest.TestCase):
    def test_arm_single_use(self):
        with tempfile.TemporaryDirectory() as t:
            s=Store(Path(t)/'test.db')
            self.assertFalse(s.consume_console_arm())
            s.arm_console(60)
            self.assertTrue(s.consume_console_arm())
            self.assertFalse(s.consume_console_arm())

    def test_expired_arm(self):
        with tempfile.TemporaryDirectory() as t:
            s=Store(Path(t)/'test.db')
            with patch('klipper_telegram.core.time.time',return_value=100):
                s.arm_console(10)
            with patch('klipper_telegram.core.time.time',return_value=111):
                self.assertFalse(s.consume_console_arm())

    def test_arm_duration_validation(self):
        with tempfile.TemporaryDirectory() as t:
            s=Store(Path(t)/'test.db')
            for seconds in (0,-1,301):
                with self.assertRaises(ValueError):
                    s.arm_console(seconds)

    def test_global_attempt_budget(self):
        with tempfile.TemporaryDirectory() as t:
            s=Store(Path(t)/'test.db')
            code,_=s.generate()
            for i in range(30):
                self.assertFalse(s.claim('BAD-CODE',100+i,100+i,'bot')[0])
            self.assertEqual(s.claim(code,999,999,'valid')[1],'rate_limited')
            new,_=s.generate()
            self.assertTrue(s.claim(new,999,999,'valid')[0])
