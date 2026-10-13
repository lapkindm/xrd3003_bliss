"""Abort scans when the X-ray interlock has acted.

The generator's hardware interlock is the safety: opening a door closes the
shutter and switches high voltage off (seen on 2026-10-06). This preset only
makes Bliss notice it and stop the scan with a clear message instead of
counting on without beam.

A scan is watched if the shutter is open when it starts. Then, at the start of
every point and at the end of the scan, it checks that both doors read closed
(PNOZ), the shutter is still open and high voltage is still on. If not, it
sends CS (so the shutter cannot re-open by itself once the door is closed) and
raises InterlockAbort, which aborts the scan.

In the session setup::

    from xrd3003_bliss.interlock import InterlockPreset
    DEFAULT_CHAIN.add_preset(InterlockPreset("shutter", "doors", "generator"), name="interlock")
"""

from __future__ import annotations

from bliss.common.logtools import log_warning
from bliss.scanning.chain import ChainIterationPreset, ChainPreset


class InterlockAbort(RuntimeError):
    """The interlock broke during a scan with the shutter open."""


class InterlockPreset(ChainPreset):
    def __init__(self, shutter: str = "shutter", doors: str | None = "doors", generator: str | None = "generator"):
        self._names = (shutter, doors, generator)
        self.armed = False
        self.checks = 0  # number of checks done (for tests and diagnostics)

    def _objects(self):
        from bliss.config.static import get_config

        cfg = get_config()
        return tuple(cfg.get(n) if n else None for n in self._names)

    # -- ChainPreset -------------------------------------------------------

    def prepare(self, chain):
        shutter, _, _ = self._objects()
        self.armed = shutter.is_open

    def get_iterator(self, chain):
        while True:
            yield _PointCheck(self)

    def stop(self, chain):
        if self.armed:
            self.check()
        self.armed = False

    # -- check -------------------------------------------------------------

    def problems(self) -> list[str]:
        shutter, doors, generator = self._objects()
        found = []
        if doors is not None:
            try:
                found += [f"{d} {s}" for d, s in doors.state().items() if s != "closed"]
            except Exception as exc:
                found.append(f"door state unknown ({exc})")
        try:
            if not shutter.is_open:
                found.append(f"shutter {shutter.state_string}")
        except Exception as exc:
            found.append(f"shutter state unknown ({exc})")
        if generator is not None:
            try:
                if not generator.hv_is_on:
                    found.append("high voltage off")
            except Exception as exc:
                found.append(f"generator state unknown ({exc})")
        return found

    def check(self) -> None:
        self.checks += 1
        found = self.problems()
        if not found:
            return
        self.armed = False
        shutter, _, _ = self._objects()
        try:
            shutter._write(f"CS:{shutter._number}")  # withdraw the open command; no waiting
        except Exception as exc:
            log_warning(shutter, "could not send CS after an interlock: %s", exc)
        raise InterlockAbort("scan aborted (interlock): " + ", ".join(found))


class _PointCheck(ChainIterationPreset):
    def __init__(self, parent: InterlockPreset):
        self._parent = parent

    def start(self):
        if self._parent.armed:
            self._parent.check()
