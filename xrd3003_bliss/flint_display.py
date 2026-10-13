"""Keep Flint visible during scans, as spec's plot window was, and show its
live scan tab when a scan starts (deriv() and other commands open their own
tabs, and Flint does not switch back by itself).

GNOME on Wayland does not let an application bring its own window to the
front (it shows "Flint is ready" instead), so Flint cannot pop up at each
scan. What it allows is a window that stays on top: this preset switches
Flint's main window to "always on top" once per Flint process, in the
background at the start of a scan (the first scan of a session starts
Flint). Keyboard focus stays where it is.

In the session setup::

    from xrd3003_bliss.flint_display import FlintOnTopPreset
    DEFAULT_CHAIN.add_preset(FlintOnTopPreset(), name="flint_on_top")

``flint_on_top(False)`` / ``flint_on_top(True)`` switch it for the session
(kept in Redis) and apply it to the running Flint at once.
"""

from __future__ import annotations

import os
import subprocess

import gevent

from bliss.common.logtools import log_warning
from bliss.config.settings import SimpleSetting
from bliss.scanning.chain import ChainPreset

STAYS_ON_TOP = 0x00040000  # Qt.WindowStaysOnTopHint
WAIT_FOR_FLINT = 30.0  # s: the first scan starts Flint, which takes a few s


def _setting(session_name: str) -> SimpleSetting:
    return SimpleSetting(f"{session_name}:flint_on_top", default_value=True)


def _get_flint():
    """The running Flint of this session, or None (never starts one)."""
    from bliss.common.plot import get_flint

    return get_flint(creation_allowed=False, mandatory=False)


def apply(flint, on_top: bool) -> bool:
    """Set or clear "always on top" on Flint's main window. Returns False if
    Flint has no plot window yet (just started).

    The flag is always cleared first: GNOME can drop the on-top state (e.g.
    when the window is maximised or tiled) while Qt still has the flag set,
    and setting it again would then change nothing (seen 2026-10-09)."""
    plot = flint.get_default_live_scan_plot("curve")
    if plot is None:
        return False
    flint.run_method(plot, "window().setWindowFlag", [STAYS_ON_TOP, False], {})
    if on_top:
        flint.run_method(plot, "window().setWindowFlag", [STAYS_ON_TOP, True], {})
    flint.run_method(plot, "window().show", [], {})  # changing the flag hides the window
    return True


def is_on_top(pid: int) -> bool | None:
    """Whether Flint's window (process ``pid``) is really kept on top, read
    from the X server (Flint is an X11 window). None if it cannot be told
    (no xwininfo/xprop, no display, window not found)."""
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    try:
        tree = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True,
                              timeout=3, env=env).stdout
        ids = [line.split()[0] for line in tree.splitlines()
               if f'"Flint (PID={pid})' in line and "mutter-x11-frames" not in line]
        if not ids:
            return None
        for wid in ids:
            out = subprocess.run(["xprop", "-id", wid, "_NET_WM_STATE"], capture_output=True,
                                 text=True, timeout=3, env=env).stdout
            if "_NET_WM_STATE_ABOVE" in out:
                return True
        return False
    except Exception:
        return None


class FlintOnTopPreset(ChainPreset):
    def __init__(self, session_name: str = "xrd"):
        self._enabled = _setting(session_name)
        self._done_pid = None  # Flint process already switched
        self._task = None

    @property
    def enabled(self) -> bool:
        return bool(self._enabled.get())

    def prepare(self, chain):
        if self._task is not None and not self._task.ready():
            return
        self._task = gevent.spawn(self._ensure)

    def _ensure(self):
        """In the background: wait for Flint (the first scan starts it), set
        it on top once per Flint process, and show its live scan tab."""
        try:
            for _ in range(int(WAIT_FOR_FLINT)):
                flint = _get_flint()
                plot = flint.get_default_live_scan_plot("curve") if flint is not None else None
                if plot is not None:
                    if self.enabled:
                        # re-apply when the window lost the state; without X
                        # tools, once per Flint process
                        state = is_on_top(flint.pid)
                        if state is False or (state is None and flint.pid != self._done_pid):
                            if apply(flint, True):
                                self._done_pid = flint.pid
                    flint.set_plot_focus(plot)  # the live scan tab, inside Flint only
                    return
                gevent.sleep(1.0)
        except Exception as exc:  # never disturb the scan
            log_warning(self, "Flint display: %s", exc)

    def set(self, on_top: bool):
        """flint_on_top(): store the choice and apply it to the running Flint."""
        self._enabled.set(bool(on_top))
        flint = _get_flint()
        if flint is not None and apply(flint, on_top):
            self._done_pid = flint.pid if on_top else None
        print(f"Flint always on top: {'ON' if on_top else 'off'}")
