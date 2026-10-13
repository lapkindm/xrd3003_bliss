"""FlintOnTopPreset with a fake Flint (no Bliss session, no display)."""

import unittest
from unittest import mock

import gevent

from xrd3003_bliss import flint_display as fd

ON = [("live:0", "window().setWindowFlag", (fd.STAYS_ON_TOP, False)),
      ("live:0", "window().setWindowFlag", (fd.STAYS_ON_TOP, True)),
      ("live:0", "window().show", ())]
FOCUS = [("live:0", "focus", ())]


class FakeFlint:
    def __init__(self, pid=100, plot="live:0"):
        self.pid, self.plot, self.calls = pid, plot, []

    def get_default_live_scan_plot(self, kind):
        return self.plot

    def run_method(self, plot, method, args, kwargs):
        self.calls.append((plot, method, tuple(args)))

    def set_plot_focus(self, plot):
        self.calls.append((plot, "focus", ()))


class FakeSetting:
    def __init__(self, value=True):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def make_preset(enabled=True):
    with mock.patch.object(fd, "_setting", return_value=FakeSetting(enabled)):
        return fd.FlintOnTopPreset("test")


class Preset(unittest.TestCase):
    def run_prepare(self, preset):
        preset.prepare(None)
        if preset._task is not None:
            preset._task.join(timeout=5)

    def scans(self, preset, flint, states):
        """One scan per X window state reported (True / False / None = unknown)."""
        for state in states:
            with mock.patch.object(fd, "_get_flint", return_value=flint), \
                    mock.patch.object(fd, "is_on_top", return_value=state):
                self.run_prepare(preset)

    def test_unknown_state_once_per_flint_process(self):
        flint = FakeFlint()
        self.scans(make_preset(), flint, [None, None])
        self.assertEqual(flint.calls, ON + FOCUS + FOCUS)

    def test_reapplied_when_the_window_lost_it(self):  # GNOME dropped it, Qt still had the flag
        flint = FakeFlint()
        self.scans(make_preset(), flint, [None, True, False, True])
        self.assertEqual(flint.calls, ON + FOCUS + FOCUS + ON + FOCUS + FOCUS)

    def test_new_flint_process_is_switched_again(self):
        preset = make_preset()
        a, b = FakeFlint(pid=1), FakeFlint(pid=2)
        self.scans(preset, a, [None])
        self.scans(preset, b, [None])
        self.assertEqual(a.calls, ON + FOCUS)
        self.assertEqual(b.calls, ON + FOCUS)

    def test_waits_for_flint_to_start(self):
        flint = FakeFlint()
        answers = [None, None, flint]
        preset = make_preset()
        with mock.patch.object(fd, "_get_flint", side_effect=lambda: answers.pop(0) if len(answers) > 1 else answers[0]), \
                mock.patch.object(fd, "is_on_top", return_value=None), \
                mock.patch.object(fd.gevent, "sleep", lambda s: gevent.idle()):
            self.run_prepare(preset)
        self.assertEqual(flint.calls, ON + FOCUS)

    def test_on_top_off_still_shows_the_live_tab(self):
        flint = FakeFlint()
        self.scans(make_preset(enabled=False), flint, [False])
        self.assertEqual(flint.calls, FOCUS)

    def test_errors_do_not_reach_the_scan(self):
        preset = make_preset()
        broken = FakeFlint()
        broken.run_method = mock.Mock(side_effect=RuntimeError("rpc timeout"))
        with mock.patch.object(fd, "_get_flint", return_value=broken), \
                mock.patch.object(fd, "is_on_top", return_value=None), \
                mock.patch.object(fd, "log_warning") as warn:
            self.run_prepare(preset)
        warn.assert_called_once()

    def test_switch_off_clears_the_flag(self):
        flint = FakeFlint()
        preset = make_preset()
        with mock.patch.object(fd, "_get_flint", return_value=flint):
            preset.set(False)
        self.scans(preset, flint, [False])  # disabled now: no change at the next scan
        self.assertFalse(preset.enabled)
        self.assertEqual(flint.calls, [ON[0], ON[2]] + FOCUS)

    def test_is_on_top_without_x_tools(self):
        with mock.patch.object(fd.subprocess, "run", side_effect=FileNotFoundError("xwininfo")):
            self.assertIsNone(fd.is_on_top(123))


if __name__ == "__main__":
    unittest.main()
