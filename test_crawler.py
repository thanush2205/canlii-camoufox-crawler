"""Offline regression checks; never contacts CanLII or a proxy."""
import multiprocessing
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch
import canlii_crawler as c
from summarize_crawl import summarize


class FakePage:
    def goto(self, *args, **kwargs):
        pass

    def content(self):
        return 'access is restricted'


class CrawlerTests(unittest.TestCase):
    def test_restricted_page_is_not_success(self):
        with patch.object(c.time, 'sleep'):
            self.assertEqual(c.wait_self_clear(FakePage(), 'https://example.test'),
                             (False, 'restricted (burned exit IP)'))

    def test_failed_tuple_stops_wall_retry(self):
        with patch.object(c, 'fetch_js', return_value={'status': 403, 'text': 'captcha-delivery.com/interstitial'}) as fetch:
            with patch.object(c, 'wait_self_clear', return_value=(False, 'blocked')):
                self.assertIsNone(c.fetch_with_wall_retry(FakePage(), 'https://example.test'))
        self.assertEqual(fetch.call_count, 1)

    def test_total_session_cap_includes_discovery_failures(self):
        with tempfile.TemporaryDirectory() as d:
            fake_api = types.SimpleNamespace(Camoufox=object())
            with patch.dict(sys.modules, {'camoufox.sync_api': fake_api}):
                with patch.object(c, 'OUT', Path(d)), patch.object(c, 'MAX_SESSIONS', 0):
                    with self.assertRaisesRegex(SystemExit, 'Total browser session limit'):
                        c.main()

    def test_concurrent_goal_and_corrupt_file_detection(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(c, 'OUT', Path(d)), patch.object(c, 'GOAL', 5):
                def writer(i):
                    c.store(f'<div id="judgment">decision {i}</div>'.encode(),
                            {'status': 'ok', 'url': f'https://example.test/{i}', 'at': c.utc_now()})
                ctx = multiprocessing.get_context('fork')
                ps = [ctx.Process(target=writer, args=(i,)) for i in range(15)]
                for p in ps:
                    p.start()
                for p in ps:
                    p.join()
                    self.assertEqual(p.exitcode, 0)
                self.assertEqual(len(c.load_seen()), 5)
                self.assertEqual(summarize(Path(d), 5), 0)
                next(Path(d).glob('objects/*/*.html')).write_text('corrupt')
                self.assertEqual(summarize(Path(d), 5), 1)


if __name__ == '__main__':
    unittest.main()
