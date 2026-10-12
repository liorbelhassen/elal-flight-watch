import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import gflights
import watch
from logic import AVAILABLE, ABSENT, UNKNOWN

ROOT = Path(__file__).parents[1]


def page(date):
    return gflights.parse((ROOT / 'tests' / 'fixtures' / f'google-{date}.html').read_text())


class RunnerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for name, value in [('STATE', root / 'state.json'), ('REPORT', root / 'diagnostics')]:
            p = patch.object(watch, name, value); p.start(); self.addCleanup(p.stop)
        self.c = dict(json.loads((ROOT / 'config.json').read_text()), date='2026-11-03')

    def run_with(self, *results):
        with patch.object(gflights, 'search', side_effect=list(results)), patch.object(watch, 'send') as send:
            code = watch.run_check(self.c, sleep=lambda s: None)
        return code, send

    def test_available_alert_sent_once_across_two_runs(self):
        code, send = self.run_with(page('2026-11-03'), page('2026-11-03'))
        self.assertEqual((code, send.call_count), (0, 1))
        self.assertIn('₪632', send.call_args.args[0])
        self.assertEqual(json.loads(watch.STATE.read_text())['last_known'], AVAILABLE)
        code, send = self.run_with(page('2026-11-03'), page('2026-11-03'))
        send.assert_not_called()

    def test_unconfirmed_positive_does_not_alert(self):
        code, send = self.run_with(page('2026-11-03'), {'ok': False, 'reason': 'Google Flights HTTP 429.'})
        self.assertEqual(code, 2); send.assert_not_called()

    def test_three_failures_send_one_error(self):
        sends = 0
        for _ in range(4):
            code, send = self.run_with({'ok': False, 'reason': 'Connection failed (TimeoutError).'})
            sends += send.call_count
        self.assertEqual((code, sends), (2, 1))
        self.assertEqual(json.loads(watch.STATE.read_text())['errors'], 4)

    def test_send_failure_does_not_acknowledge(self):
        with patch.object(gflights, 'search', return_value=page('2026-11-03')), \
             patch.object(watch, 'send', side_effect=RuntimeError('Telegram HTTP 500')):
            with self.assertRaises(RuntimeError): watch.run_check(self.c, sleep=lambda s: None)
        self.assertFalse(watch.STATE.exists())

    def test_not_offered_sends_heartbeat_then_quiet(self):
        self.c['date'] = '2026-11-02'
        code, send = self.run_with(page('2026-11-02'))
        self.assertEqual((code, send.call_count), (0, 1))
        self.assertEqual(json.loads(watch.STATE.read_text())['last_known'], ABSENT)
        code, send = self.run_with(page('2026-11-02'))
        send.assert_not_called()

    def test_bootstrap_only_exact_private_recipient_and_no_id_in_log(self):
        def message(user, chat, text='/start LY385'):
            return {'message': {'from': user, 'chat': chat, 'text': text, 'date': time.time()}}
        updates = [message({'id': 5, 'username': 'other'}, {'type': 'private', 'id': 5}),
                   message({'id': 6, 'username': 'liortest'}, {'type': 'group', 'id': 99}),
                   message({'id': 777, 'username': 'liortest'}, {'type': 'private', 'id': 777})]
        with patch.object(watch, 'telegram', side_effect=[updates, {}]) as api, patch('builtins.print') as out:
            watch.bootstrap('@LiorTest')
        self.assertEqual(api.call_args.args[1]['chat_id'], 777)
        self.assertNotIn('777', str(out.call_args_list))


if __name__ == '__main__': unittest.main()
