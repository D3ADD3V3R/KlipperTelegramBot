import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from klipper_telegram.core import Store, render

class PairingTests(unittest.TestCase):
    def test_pair_once(self):
        with tempfile.TemporaryDirectory() as temp:
            s=Store(Path(temp)/'store.db')
            code,_=s.generate(600)
            self.assertTrue(s.claim(code,42,42,'someone')[0])
            self.assertFalse(s.claim(code,43,43,'another')[0])
            self.assertTrue(s.is_subscribed(42,42))
            self.assertFalse(s.is_subscribed(42,99))
            self.assertTrue(s.unsubscribe(42))
            self.assertFalse(s.is_subscribed(42,42))

    def test_replacement_and_expiration(self):
        with tempfile.TemporaryDirectory() as temp:
            s=Store(Path(temp)/'store.db')
            a,_=s.generate(600)
            b,_=s.generate(600)
            self.assertFalse(s.claim(a,1,1,'a')[0])
            self.assertTrue(s.claim(b,2,2,'b')[0])
            c,_=s.generate(-1)
            self.assertEqual(s.expire(),1)
            self.assertFalse(s.claim(c,3,3,'c')[0])

    def test_template_whitelist(self):
        self.assertEqual(render('Hello {user} {unknown}',{'user':'@test'}),'Hello @test {unknown}')

if __name__ == '__main__': unittest.main()
