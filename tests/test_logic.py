import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

import gflights
from logic import AVAILABLE, ABSENT, UNKNOWN, decide, expired, event_for, next_state

ROOT = Path(__file__).parents[1]


def fixture(date):
    return gflights.parse((ROOT / 'tests' / 'fixtures' / f'google-{date}.html').read_text())


class FlightDecisionTests(unittest.TestCase):
    """Fixtures are trimmed copies of real Google Flights responses captured 2026-10-12."""

    def setUp(self):
        self.c = json.loads((ROOT / 'config.json').read_text())
        self.control = dict(self.c, date='2026-11-03')
        self.offered = fixture('2026-11-03')
        self.target = next(o for o in self.offered['offers'] if o['segments'][0]['number'] == '385')

    def test_query_encoding_matches_observed_google_url(self):
        self.assertEqual(gflights.tfs('2026-11-02', 'TLV', 'FCO', 1),
                         'GhoSCjIwMjYtMTEtMDJqBRIDVExWcgUSA0ZDT0IBAUgBmAEC')

    def test_real_target_date_without_ly385_is_not_offered(self):
        result = fixture('2026-11-02')
        self.assertIn('383', [o['segments'][0]['number'] for o in result['offers']])
        self.assertEqual(decide(self.c, result)[0], ABSENT)

    def test_real_positive_control_is_available(self):
        status, _, offer, _ = decide(self.control, self.offered)
        self.assertEqual((status, offer['price'], offer['segments'][0]['departure']), (AVAILABLE, 632, '06:10'))

    def test_wrong_date_results_are_unknown(self):
        self.assertEqual(decide(self.c, self.offered)[0], UNKNOWN)

    def test_wrong_destination_is_unknown(self):
        self.target['segments'][0]['destination'] = 'CIA'
        self.assertEqual(decide(self.control, self.offered)[0], UNKNOWN)

    def test_changed_time_is_mismatch_not_available(self):
        self.target['segments'][0]['departure'] = '07:30'
        self.assertEqual(decide(self.control, self.offered)[3], 'mismatch')
        self.assertEqual(decide(self.control, self.offered)[0], UNKNOWN)

    def test_missing_or_zero_price_is_unknown(self):
        for price in (None, 0):
            self.target['price'] = price
            self.assertEqual(decide(self.control, self.offered)[0], UNKNOWN)

    def test_similar_flight_number_is_not_target(self):
        self.target['segments'][0]['number'] = '3850'
        self.assertEqual(decide(self.control, self.offered)[0], ABSENT)

    def test_zero_padded_number_matches(self):
        self.target['segments'][0]['number'] = '0385'
        self.assertEqual(decide(self.control, self.offered)[0], AVAILABLE)

    def test_connection_or_other_airline_is_not_available(self):
        connecting = copy.deepcopy(self.target)
        connecting['segments'][0]['destination'] = 'ATH'
        connecting['segments'].append(dict(self.target['segments'][0], origin='ATH', carrier='A3', number='1'))
        self.offered['offers'].remove(self.target)
        self.offered['offers'].append(connecting)
        self.assertEqual(decide(self.control, self.offered)[0], UNKNOWN)

    def test_block_and_changed_page_are_unknown(self):
        for html in ('', '<html>Our systems have detected unusual traffic</html>', '<html>new layout</html>'):
            self.assertEqual(decide(self.c, gflights.parse(html))[0], UNKNOWN)

    def test_failed_search_is_unknown(self):
        self.assertEqual(decide(self.c, {'ok': False, 'reason': 'Google Flights HTTP 429.'})[0], UNKNOWN)

    def test_cutoff(self):
        self.assertFalse(expired(self.c, datetime(2026, 11, 2, 4, 9, tzinfo=timezone.utc)))
        self.assertTrue(expired(self.c, datetime(2026, 11, 2, 4, 10, tzinfo=timezone.utc)))


class EventTests(unittest.TestCase):
    def test_alert_once_then_reopen_after_not_offered(self):
        s, t = {}, 1_000_000
        e = event_for(s, AVAILABLE, t); self.assertEqual(e, 'available'); s = next_state(s, AVAILABLE, t, e)
        self.assertIsNone(event_for(s, AVAILABLE, t + 3600))
        s = next_state(s, UNKNOWN, t + 7200)
        self.assertIsNone(event_for(s, AVAILABLE, t + 10800))
        s = next_state(s, ABSENT, t + 14400)
        self.assertEqual(event_for(s, AVAILABLE, t + 18000), 'available')

    def test_error_after_three_unknown_once_per_day(self):
        s, t, events = {}, 1_000_000, []
        for i in range(6):
            e = event_for(s, UNKNOWN, t + i * 3600); events.append(e); s = next_state(s, UNKNOWN, t + i * 3600, e)
        self.assertEqual(events, [None, None, 'error', None, None, None])

    def test_mismatch_notice_once_per_day(self):
        s = next_state({}, UNKNOWN, 0, 'mismatch')
        self.assertNotEqual(event_for(s, UNKNOWN, 3600, 'mismatch'), 'mismatch')
        self.assertEqual(event_for(s, UNKNOWN, 90000, 'mismatch'), 'mismatch')

    def test_daily_heartbeat(self):
        s = next_state({}, ABSENT, 0, 'heartbeat')
        self.assertIsNone(event_for(s, ABSENT, 3600))
        self.assertEqual(event_for(s, ABSENT, 86400), 'heartbeat')


if __name__ == '__main__': unittest.main()
