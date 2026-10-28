"""FlintPopupPreset with a fake Flint and a fake X server (no Bliss session, no display)."""

import unittest
from unittest import mock

from xrd3003_bliss import flint_display as fd

CLEAR = [("live:0", "window().setWindowFlag", (fd.STAYS_ON_TOP, False)),
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


class FakeX:
    def __init__(self):
        self.raised = []

    def raise_window(self, w):
        self.raised.append(w)


def make_preset(enabled=True):
    with mock.patch.object(fd, "_setting", return_value=FakeSetting(enabled)):
        p = fd.FlintPopupPreset("test")
    p._x = FakeX()
    return p


class Preset(unittest.TestCase):
    def setUp(self):
        self.windows = mock.patch.object(fd, "flint_windows", side_effect=lambda pid: [pid * 10])
        self.windows.start()

    def tearDown(self):
        self.windows.stop()

    def scan_start(self, preset, flint):
        with mock.patch.object(fd, "_get_flint", return_value=flint):
            preset.prepare(None)
            preset._task.join(timeout=5)

    def points(self, preset, flint, n):
        with mock.patch.object(fd, "_get_flint", return_value=flint):
            it = preset.get_iterator(None)
            for _ in range(n):
                next(it).stop()
                if preset._pop is not None:
                    preset._pop.join(timeout=5)

    def test_scan_start_clears_old_on_top_once_shows_live_tab_and_raises(self):
        p, flint = make_preset(), FakeFlint()
        self.scan_start(p, flint)
        self.scan_start(p, flint)
        self.assertEqual(flint.calls, CLEAR + FOCUS + FOCUS)   # flag cleared once per Flint process
        self.assertEqual(p._x.raised, [1000, 1000])            # raised at each scan start

    def test_new_flint_process_cleared_again(self):
        p = make_preset()
        self.scan_start(p, FakeFlint(pid=100))
        f2 = FakeFlint(pid=200)
        self.scan_start(p, f2)
        self.assertEqual(f2.calls, CLEAR + FOCUS)
        self.assertEqual(p._x.raised, [1000, 2000])

    def test_after_each_point_at_most_once_per_interval(self):
        p, flint = make_preset(), FakeFlint()
        with mock.patch.object(fd.time, "monotonic", side_effect=[10.0, 10.0, 10.5, 11.2, 11.2]):
            self.points(p, flint, 3)   # t = 10.0 raise, 10.5 too soon, 11.2 raise
        self.assertEqual(p._x.raised, [1000, 1000])

    def test_disabled_never_raises_but_still_shows_live_tab(self):
        p, flint = make_preset(enabled=False), FakeFlint()
        self.scan_start(p, flint)
        self.points(p, flint, 3)
        self.assertEqual(p._x.raised, [])
        self.assertIn(("live:0", "focus", ()), flint.calls)

    def test_no_flint_window_found(self):
        p, flint = make_preset(), FakeFlint()
        with mock.patch.object(fd, "flint_windows", return_value=[]):
            self.scan_start(p, flint)
        self.assertEqual(p._x.raised, [])

    def test_errors_never_reach_the_scan(self):
        p, flint = make_preset(), FakeFlint()
        p._x.raise_window = mock.Mock(side_effect=OSError("no display"))
        with mock.patch.object(fd, "log_warning") as warn:
            self.scan_start(p, flint)
            self.points(p, flint, 1)
        self.assertEqual(warn.call_count, 2)

    def test_set_switches_the_session_setting(self):
        p = make_preset()
        p.set(False)
        self.assertFalse(p.enabled)
        p.set(True)
        self.assertTrue(p.enabled)


class XMessage(unittest.TestCase):
    def test_above_on_then_off_as_pager_request(self):
        x = fd.X11.__new__(fd.X11)
        sent = []
        x.dpy, x.root, x.state, x.above = 1, 2, 300, 301
        x.x = mock.Mock()
        x.x.XSendEvent.side_effect = lambda dpy, root, prop, mask, ev: sent.append(
            (ev._obj.xclient.window, ev._obj.xclient.message_type, list(ev._obj.xclient.data)))
        x.raise_window(0x1800016)
        self.assertEqual(sent, [(0x1800016, 300, [1, 301, 0, 2, 0]), (0x1800016, 300, [0, 301, 0, 2, 0])])


if __name__ == "__main__":
    unittest.main()
