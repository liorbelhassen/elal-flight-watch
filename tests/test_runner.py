import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import watch
from logic import AVAILABLE, ABSENT, UNKNOWN


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        for name,value in [('STATE',root/'state.json'),('REPORT',root/'diagnostics')]:
            p = patch.object(watch,name,value)
            p.start()
            self.addCleanup(p.stop)
        self.config={'calibrated':True}

    def test_timeout_advances_failure_counter_and_alerts(self):
        with patch.object(watch,'inspect_once',side_effect=TimeoutError), patch.object(watch,'send') as send:
            for _ in range(3):
                self.assertEqual(watch.run_check(self.config,'https://booking.elal.com/'),2)
            self.assertEqual(send.call_count,1)
            self.assertEqual(json.loads(watch.STATE.read_text())['errors'],3)

    def test_false_positive_second_pass_does_not_alert(self):
        with patch.object(watch,'inspect_once',side_effect=[(AVAILABLE,{'status':AVAILABLE}),(ABSENT,{})]),patch.object(watch,'send') as send:
            self.assertEqual(watch.run_check(self.config,'https://booking.elal.com/'),2)
            send.assert_not_called()

    def test_confirmation_timeout_does_not_alert(self):
        with patch.object(watch,'inspect_once',side_effect=[(AVAILABLE,{'status':AVAILABLE}),TimeoutError]),patch.object(watch,'send') as send:
            self.assertEqual(watch.run_check(self.config,'https://booking.elal.com/'),2)
            send.assert_not_called()

    def test_send_failure_does_not_acknowledge_notification(self):
        with patch.object(watch,'inspect_once',return_value=(AVAILABLE,{'status':AVAILABLE})),patch.object(watch,'send',side_effect=RuntimeError('No notification')):
            with self.assertRaises(RuntimeError): watch.run_check(self.config,'https://booking.elal.com/')
            self.assertFalse(watch.STATE.exists())

    def test_untrusted_search_host_rejected(self):
        with patch.dict(os.environ,{'SEARCH_URL':'https://other.example/'}):
            with self.assertRaises(RuntimeError): watch.search_url('check')

    def test_bootstrap_only_exact_private_recipient(self):
        import time
        def message(user,chat,text='/start LY385'):
            return {'message':{'from':user,'chat':chat,'text':text,'date':time.time()}}
        updates=[
            message({'id':5,'username':'other'},{'type':'private','id':5}),
            message({'id':6,'username':'liortest'},{'type':'group','id':99}),
            message({'id':7,'username':'liortest'},{'type':'private','id':7})]
        with patch.object(watch,'telegram',side_effect=[updates,{}]) as api:
            watch.bootstrap('@LiorTest')
            self.assertEqual(api.call_args.args[1]['chat_id'],7)


if __name__=='__main__': unittest.main()
