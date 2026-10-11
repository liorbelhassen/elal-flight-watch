import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from logic import AVAILABLE, ABSENT, UNKNOWN, decide, expired, event_for, next_state


class FlightDecisionTests(unittest.TestCase):
    def setUp(self):
        self.c = json.loads((Path(__file__).parents[1] / 'config.json').read_text())
        self.c.update(calibrated=True, direct_flight_pattern=r'Nonstop')
        # Fictional fixture, not verified EL AL DOM selectors or actual availability.
        self.summary = 'TLV FCO 2026-11-02 1 adult One way Economy'
        self.c['query_checks'] = [
            {'field': f, 'pattern': p} for f, p in [
                ('origin',r'\bTLV\b'), ('destination',r'\bFCO\b'),
                ('date',r'\b2026-11-02\b'), ('adults',r'\b1 adult\b'),
                ('one_way',r'One way'), ('cabin',r'Economy')]]
        self.card = {'text': 'LY385 TLV FCO 06:10 Nonstop', 'price':'USD 300',
                     'button_visible':True, 'button_enabled':True}

    def status(self, cards=None, **kw):
        return decide(self.c, self.summary, cards if cards is not None else [self.card], **kw)[0]

    def test_positive_offer(self): self.assertEqual(self.status(), AVAILABLE)
    def test_no_target_after_verified_search(self): self.assertEqual(self.status([]), ABSENT)
    def test_blocked_is_not_full(self): self.assertEqual(self.status(blocked=True), UNKNOWN)
    def test_incomplete_is_unknown(self): self.assertEqual(self.status(ready=False), UNKNOWN)
    def test_disabled_by_default(self):
        self.c['calibrated'] = False
        self.assertEqual(self.status(), UNKNOWN)
    def test_wrong_date_is_unknown(self):
        self.summary=self.summary.replace('11-02','11-03')
        self.assertEqual(self.status(), UNKNOWN)
    def test_wrong_passengers(self):
        self.summary=self.summary.replace('1 adult','2 adults')
        self.assertEqual(self.status(), UNKNOWN)
    def test_round_trip(self):
        self.summary=self.summary.replace('One way','Round trip')
        self.assertEqual(self.status(), UNKNOWN)
    def test_missing_context_check(self):
        self.c['query_checks'].pop()
        self.assertEqual(self.status(), UNKNOWN)
    def test_similar_flight_not_match(self):
        self.card['text']='LY3850 TLV FCO 06:10 Nonstop'
        self.assertEqual(self.status(), ABSENT)
    def test_afternoon_flight_not_match(self):
        self.card['text']='LY383 TLV FCO 18:15 Nonstop'
        self.assertEqual(self.status(), ABSENT)
    def test_wrong_route(self):
        self.card['text']='LY385 FCO TLV2 06:10 Nonstop'
        self.assertEqual(self.status(), UNKNOWN)
    def test_disabled_button(self):
        self.card['button_enabled']=False
        self.assertEqual(self.status(), UNKNOWN)
    def test_hidden_button(self):
        self.card['button_visible']=False
        self.assertEqual(self.status(), UNKNOWN)
    def test_sold_out_card(self):
        self.card['sold_out']=True
        self.assertEqual(self.status(), UNKNOWN)
    def test_no_price(self):
        self.card['price']=''
        self.assertEqual(self.status(), UNKNOWN)
    def test_zero_price(self):
        self.card['price']='USD 0'
        self.assertEqual(self.status(), UNKNOWN)
    def test_number_without_currency(self):
        self.card['price']='300'
        self.assertEqual(self.status(), UNKNOWN)
    def test_connection_not_match(self):
        self.card['text']='LY385 TLV FCO 06:10 Via Athens'
        self.assertEqual(self.status(), UNKNOWN)
    def test_unknown_does_not_rearm_duplicate(self):
        state=next_state({'last_known':AVAILABLE}, UNKNOWN, 1000)
        self.assertIsNone(event_for(state, AVAILABLE, 1100))
    def test_reopens_after_absence(self):
        state=next_state({'last_known':AVAILABLE}, ABSENT, 1000)
        self.assertEqual(event_for(state, AVAILABLE, 1100),'available')
    def test_failure_alert_after_three(self):
        self.assertEqual(event_for({'errors':2},UNKNOWN,100000),'error')
        self.assertIsNone(event_for({'errors':1},UNKNOWN,100000))
    def test_error_cooldown(self):
        self.assertIsNone(event_for({'errors':10,'last_error_notice':100000},UNKNOWN,100100))
    def test_cutoff(self):
        self.assertFalse(expired(self.c,datetime(2026,11,2,4,9,tzinfo=timezone.utc)))
        self.assertTrue(expired(self.c,datetime(2026,11,2,4,10,tzinfo=timezone.utc)))


if __name__ == '__main__': unittest.main()
