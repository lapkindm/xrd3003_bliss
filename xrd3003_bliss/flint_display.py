"""Bring Flint to the front at the start of each scan and after every point,
as spec's plot window did, without keeping it above all other windows; and
show its live scan tab when a scan starts (deriv() and other commands open
their own tabs, and Flint does not switch back by itself).

GNOME on Wayland does not let an application bring its own window to the
front (it shows "Flint is ready" instead). Flint is an X11 window, and GNOME
does honour a request to put an X11 window "above" others: this preset sends
"above on" and at once "above off" (EWMH _NET_WM_STATE, the way a pager or
taskbar does). GNOME raises the window and then leaves it in the normal
stack, so clicking another window puts that one in front again. Keyboard
focus stays where it is. No extra tools: the request goes through libX11.

In the session setup::

    from xrd3003_bliss.flint_display import FlintPopupPreset
    DEFAULT_CHAIN.add_preset(FlintPopupPreset("xrd"), name="flint_popup")

``flint_popup(False)`` / ``flint_popup(True)`` switch it for the session
(kept in Redis).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
import time

import gevent

from bliss.common.logtools import log_warning
from bliss.config.settings import SimpleSetting
from bliss.scanning.chain import ChainIterationPreset, ChainPreset

STAYS_ON_TOP = 0x00040000  # Qt.WindowStaysOnTopHint (set by the earlier "always on top" version)
WAIT_FOR_FLINT = 30.0  # s: the first scan starts Flint, which takes a few s
MIN_INTERVAL = 1.0  # s: at most one pop-up per second during a scan


def _setting(session_name: str) -> SimpleSetting:
    return SimpleSetting(f"{session_name}:flint_popup", default_value=True)


def _get_flint():
    """The running Flint of this session, or None (never starts one)."""
    from bliss.common.plot import get_flint

    return get_flint(creation_allowed=False, mandatory=False)


def clear_on_top(flint) -> bool:
    """Clear Qt's "always on top" flag on Flint's main window (left by the
    earlier version of this preset). Returns False if Flint has no plot
    window yet. Changing the flag hides the window, so it is shown again."""
    plot = flint.get_default_live_scan_plot("curve")
    if plot is None:
        return False
    flint.run_method(plot, "window().setWindowFlag", [STAYS_ON_TOP, False], {})
    flint.run_method(plot, "window().show", [], {})
    return True


def _x_env() -> dict:
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    return env


def flint_windows(pid: int) -> list[int]:
    """X window ids of Flint's main window (process ``pid``), from xwininfo."""
    tree = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True,
                          timeout=3, env=_x_env()).stdout
    return [int(line.split()[0], 16) for line in tree.splitlines()
            if f'"Flint (PID={pid})' in line and "mutter-x11-frames" not in line]


class _XClientMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong),
                ("message_type", ctypes.c_ulong), ("format", ctypes.c_int),
                ("data", ctypes.c_long * 5)]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _XClientMessage), ("pad", ctypes.c_long * 24)]


class X11:
    """Minimal libX11 access: ask the window manager to raise a window by
    switching its "above" state on and off (EWMH _NET_WM_STATE)."""

    CLIENT_MESSAGE = 33
    SUBSTRUCTURE = (1 << 19) | (1 << 20)  # SubstructureNotify | SubstructureRedirect
    ADD, REMOVE = 1, 0
    SOURCE_PAGER = 2  # a pager/taskbar request: not subject to focus-stealing prevention

    def __init__(self, display_name: str | None = None):
        lib = ctypes.util.find_library("X11") or "libX11.so.6"
        self.x = ctypes.CDLL(lib)
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x.XInternAtom.restype = ctypes.c_ulong
        self.x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.x.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                                      ctypes.POINTER(_XEvent)]
        self.x.XFlush.argtypes = [ctypes.c_void_p]
        name = (display_name or _x_env()["DISPLAY"]).encode()
        self.dpy = self.x.XOpenDisplay(name)
        if not self.dpy:
            raise OSError(f"cannot open X display {name.decode()}")
        self.root = self.x.XDefaultRootWindow(self.dpy)
        self.state = self.x.XInternAtom(self.dpy, b"_NET_WM_STATE", 0)
        self.above = self.x.XInternAtom(self.dpy, b"_NET_WM_STATE_ABOVE", 0)

    def _state(self, window: int, action: int):
        ev = _XEvent()
        m = ev.xclient
        m.type, m.send_event, m.display = self.CLIENT_MESSAGE, 1, self.dpy
        m.window, m.message_type, m.format = window, self.state, 32
        m.data[0], m.data[1], m.data[2], m.data[3] = action, self.above, 0, self.SOURCE_PAGER
        self.x.XSendEvent(self.dpy, self.root, 0, self.SUBSTRUCTURE, ctypes.byref(ev))

    def raise_window(self, window: int):
        """Above on, then off: raised once, then back in the normal stack."""
        self._state(window, self.ADD)
        self.x.XFlush(self.dpy)
        self._state(window, self.REMOVE)
        self.x.XFlush(self.dpy)


class _Point(ChainIterationPreset):
    def __init__(self, preset):
        self.preset = preset

    def stop(self):  # after each point
        self.preset.pop_up()


class FlintPopupPreset(ChainPreset):
    def __init__(self, session_name: str = "xrd"):
        self._enabled = _setting(session_name)
        self._cleared_pid = None  # Flint process whose "always on top" flag was cleared
        self._windows: dict[int, list[int]] = {}  # Flint pid -> X window ids
        self._x = None
        self._last = 0.0
        self._task = None  # scan start (waits for Flint)
        self._pop = None  # one pop-up in progress

    @property
    def enabled(self) -> bool:
        return bool(self._enabled.get())

    def prepare(self, chain):
        if self._task is not None and not self._task.ready():
            return
        self._task = gevent.spawn(self._start)

    def get_iterator(self, chain):
        while True:
            yield _Point(self)

    def _start(self):
        """In the background: wait for Flint (the first scan starts it), clear
        the old always-on-top flag once per Flint process, show the live scan
        tab and bring Flint to the front."""
        try:
            for _ in range(int(WAIT_FOR_FLINT)):
                flint = _get_flint()
                plot = flint.get_default_live_scan_plot("curve") if flint is not None else None
                if plot is not None:
                    if flint.pid != self._cleared_pid and clear_on_top(flint):
                        self._cleared_pid = flint.pid
                    flint.set_plot_focus(plot)  # the live scan tab, inside Flint only
                    self._raise(flint.pid)
                    return
                gevent.sleep(1.0)
        except Exception as exc:  # never disturb the scan
            log_warning(self, "Flint display: %s", exc)

    def pop_up(self):
        """After a point: bring Flint to the front, in the background, at most
        once per MIN_INTERVAL; never blocks or disturbs the scan."""
        if not self.enabled or time.monotonic() - self._last < MIN_INTERVAL:
            return
        if self._pop is not None and not self._pop.ready():
            return
        self._last = time.monotonic()
        self._pop = gevent.spawn(self._pop_up)

    def _pop_up(self):
        try:
            flint = _get_flint()
            if flint is not None:
                self._raise(flint.pid)
        except Exception as exc:  # noqa: BLE001
            log_warning(self, "Flint display: %s", exc)

    def _raise(self, pid: int):
        if not self.enabled:
            return
        windows = self._windows.get(pid) or flint_windows(pid)
        if not windows:
            return
        self._windows = {pid: windows}
        if self._x is None:
            self._x = X11()
        for w in windows:
            self._x.raise_window(w)

    def set(self, on: bool):
        """flint_popup(): store the choice for the session."""
        self._enabled.set(bool(on))
        print(f"Flint pop-up at scan start and after each point: {'ON' if on else 'off'}")
