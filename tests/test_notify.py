"""notify / notifying with a fake sendmail."""

import email
import unittest
from unittest import mock

from xrd3003_bliss import notify as nt

ME = "responsible@uni-tuebingen.de"  # set by the session on the instrument


class Mails(unittest.TestCase):
    def setUp(self):
        self.sent = []

        def run(cmd, input, check, timeout):
            self.sent.append(email.message_from_bytes(input))

        self.patches = [mock.patch.object(nt, "ERRORS_ALWAYS_TO", ME),
                        mock.patch.object(nt.subprocess, "run", side_effect=run),
                        mock.patch.object(nt, "_context", return_value=["Data file: /x/s.h5"])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def text(self, i):
        return self.sent[i].get_payload(decode=True).decode()

    def test_finished_goes_to_the_given_addresses(self):
        with nt.notifying("spatial scan -25..25", to="a@uni-tuebingen.de") as status:
            status["point"] = "51/51 (y = 25)"
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["To"], "a@uni-tuebingen.de")
        self.assertEqual(self.sent[0]["Subject"], "[XRD-3003TT] Finished: spatial scan -25..25")
        self.assertIn("finished normally", self.text(0))
        self.assertIn("Point: 51/51 (y = 25)", self.text(0))
        self.assertIn("Data file: /x/s.h5", self.text(0))

    def test_error_goes_to_the_given_addresses_and_always_to_the_fixed_one(self):
        with self.assertRaises(RuntimeError):
            with nt.notifying("spatial scan", to="a@x.de, b@y.de") as status:
                status["point"] = "12/51 (y = -14)"
                raise RuntimeError("a2: no reply")
        self.assertEqual(self.sent[0]["To"], f"{ME}, a@x.de, b@y.de")
        self.assertEqual(self.sent[0]["Subject"], "[XRD-3003TT] STOPPED: spatial scan")
        self.assertIn("RuntimeError: a2: no reply", self.text(0))
        self.assertIn("Point: 12/51 (y = -14)", self.text(0))

    def test_without_addresses_only_errors_to_the_fixed_one(self):
        with nt.notifying("x"):
            pass
        with self.assertRaises(RuntimeError):
            with nt.notifying("x"):
                raise RuntimeError("stop")
        self.assertEqual([m["To"] for m in self.sent], [ME])

    def test_no_fixed_address_set(self):
        with mock.patch.object(nt, "ERRORS_ALWAYS_TO", None):
            self.assertEqual(nt.recipients("a@x.de", error=True), "a@x.de")
            self.assertEqual(nt.recipients(None, error=True), "")

    def test_no_duplicates(self):
        self.assertEqual(nt.recipients(f"{ME.upper()}, b@x.de, b@x.de", error=True), f"{ME}, b@x.de")

    def test_ctrl_c_sends_nothing(self):
        with self.assertRaises(KeyboardInterrupt):
            with nt.notifying("spatial scan", to="a@x.de"):
                raise KeyboardInterrupt
        self.assertEqual(self.sent, [])

    def test_ctrl_c_inside_a_scan_sends_nothing(self):  # Bliss raises ScanAbort from KeyboardInterrupt
        with self.assertRaises(nt.ScanAbort):
            with nt.notifying("spatial scan", to="a@x.de"):
                try:
                    raise KeyboardInterrupt
                except KeyboardInterrupt as e:
                    raise nt.ScanAbort from e
        self.assertEqual(self.sent, [])

    def test_python_cleanup_sends_nothing(self):
        cm = nt.notifying("x", to="a@x.de")
        cm.__enter__()
        cm.gen.close()  # what happens to an unfinished context when it is collected
        self.assertEqual(self.sent, [])

    def test_mail_failure_does_not_stop_the_program(self):
        with mock.patch.object(nt.subprocess, "run", side_effect=OSError("no sendmail")):
            self.assertFalse(nt.notify("s", "t", to="a@x.de"))
            with nt.notifying("x", to="a@x.de"):
                pass  # no exception


if __name__ == "__main__":
    unittest.main()
