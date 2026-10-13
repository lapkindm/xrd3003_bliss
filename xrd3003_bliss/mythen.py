"""Mythen of the XRD-3003TT with spec's counters, autoabsorber and
count-rate protection.

Example YAML::

    - plugin: bliss
      package: xrd3003_bliss.mythen
      class: XrdMythen
      name: mythen
      hostname: 192.168.0.90
      detector_settings: StdCu   # sent as "-settings StdCu", as spec
      rois:                      # channels [start, end), end excluded
        spec: [659, 670]         # spec ROI_SPECULAR 659:669 (inclusive)
        offspec1: [647, 658]
        offspec2: [671, 682]
      absorber: a2               # axis name (not a $reference)
      absorber_factors: [1, 7.97, 7.59, 7.7, 7.79, 7.26]   # spec absob[] (Cu)
      shutter: shutter           # closed when the count rate is too high
      masked_channels: [1271, 1273]   # noisy channels, interpolated (see below)
      autoabsorber:
        enabled: true            # default after a fresh start; then kept in Redis
        precount_time: 0.1       # s
        close_above: 70000       # peak counts in the pre-count
        upper: 12400
        lower: 1550
        max_steps: 12            # absorber changes per point before giving up

Counters, as in spec's ``user_getcounts`` / ``absorber_korrektur``
(f = absorber correction factor of the current a2 position; spec's names
in brackets)::

    peak (det)  peak of the spectrum        peak_corr (dcr)  peak * f
    spec, offspec1, offspec2 (spc, of1, of2)  ROI sums * f
    spec_corr (scr)  spec - (offspec1 + offspec2) / 2
    sum (dsm)   sum of the spectrum         abs  absorber position
    exposure    exposure time of the count (s), for normalisation; the
                autoabsorber pre-count is not included

Before each count (in a scan, after the motors reached the point), the
acquisition runs spec's ``check_rate``: a pre-count, then more or less
absorber, or close the shutter and abort when the rate is too high. The
rate protection works even with the autoabsorber off, as in spec.

``masked_channels`` are replaced by linear interpolation from the nearest
unmasked channels before the counters and the pre-count peak are computed,
as the detector's own bad-channel interpolation does. The ``spectrum``
counter keeps the raw data.
"""

from __future__ import annotations

import gevent
import numpy

from bliss.common.counter import Counter
from bliss.common.logtools import log_info, log_warning
from bliss.config.settings import SimpleSetting
from bliss.controllers.counter import counter_namespace
from bliss.controllers.mca.mythen import Mythen, MythenAcquistionSlave
from bliss.scanning.chain import AcquisitionSlave

from .absorber import AbsorberTable, Action, Thresholds, decide


class CountRateTooHigh(RuntimeError):
    """The pre-count saw too many counts: the shutter was closed."""


class SpectrumCounter(Counter):
    """A value computed from each Mythen spectrum (and the absorber factor)."""

    def __init__(self, name, controller, func, dtype=float):
        self._func = func
        self._dtype = dtype
        self.acquisition_device = None
        super().__init__(name, controller)

    @property
    def dtype(self):
        return self._dtype

    def register_device(self, device):
        self.acquisition_device = device

    def compute_roi(self, spectrum):  # name required by MythenAcquistionSlave._publish
        spectrum = self._counter_controller.masked(spectrum)
        return self._func(spectrum, self.acquisition_device)


class AutoAbsorber:
    """``mythen.autoabsorber``: spec's auto_on / auto_off and check_rate."""

    def __init__(self, mythen: XrdMythen, config: dict):
        self._mythen = mythen
        self.precount_time = float(config.get("precount_time", 0.1))
        self.thresholds = Thresholds(
            close_above=float(config.get("close_above", 70000)),
            upper=float(config.get("upper", 12400)),
            lower=float(config.get("lower", 1550)),
        )
        self.max_steps = int(config.get("max_steps", 12))
        self._enabled = SimpleSetting(f"{mythen.name}:autoabsorber_enabled",
                                      default_value=bool(config.get("enabled", True)))

    @property
    def enabled(self) -> bool:
        return bool(self._enabled.get())

    def on(self):
        """spec auto_on."""
        self._enabled.set(True)
        print("Autoabsorber activated!")

    def off(self):
        """spec auto_off (the count-rate protection stays active)."""
        self._enabled.set(False)
        print("Autoabsorber deactivated! (count-rate protection still active)")

    def __info__(self):
        t = self.thresholds
        return (
            f"autoabsorber of {self._mythen.name}: {'ON' if self.enabled else 'off'}\n"
            f"  pre-count {self.precount_time:g} s; peak > {t.close_above:g}: close {self._mythen.shutter_name} "
            f"and abort (always)\n"
            f"  peak > {t.upper:g}: more absorber; peak < {t.lower:g}: less absorber "
            f"({self._mythen.absorber_name} positions {self._mythen.table.first}..{self._mythen.table.last})"
        )

    def check(self):
        """spec check_rate: pre-count, adjust the absorber, protect the detector.

        Raises CountRateTooHigh (after closing the shutter) when the rate is
        too high even with the most absorber.
        """
        mythen = self._mythen
        absorber = mythen.absorber()
        for _ in range(self.max_steps + 1):
            peak = float(numpy.max(mythen.masked(mythen.precount(self.precount_time))))
            position = mythen.table.position(absorber.position)
            action = decide(peak, position, mythen.table, self.thresholds, self.enabled)
            if action is Action.OK:
                return
            if action is Action.CLOSE:
                mythen.close_shutter_for_rate(peak, position)
            target = position + (1 if action is Action.MORE else -1)
            print(f"Autoabsorber: peak {peak:g} counts, {absorber.name} {position} -> {target}")
            absorber.move(target)
        log_warning(mythen, "autoabsorber did not settle after %d steps; counting at %s = %g",
                    self.max_steps, absorber.name, absorber.position)


class XrdMythen(Mythen):
    def _apply_configuration(self):
        """Load the detector settings file first, exactly as spec does
        (``-settings StdCu``; Bliss's element_settings only knows "Cu"),
        then apply the corrections from the config."""
        settings = self._config.get("detector_settings")
        if settings:
            if not str(settings).isalnum():
                raise ValueError(f"{self.name}: invalid detector_settings {settings!r}")
            self._interface._run_command(f"-settings {settings}", "int")
        super()._apply_configuration()

    def __init__(self, name, config):
        super().__init__(name, config)
        self.absorber_name = config.get("absorber")
        self.shutter_name = config.get("shutter")
        for key in ("absorber", "shutter"):
            if not isinstance(config.get(key), (str, type(None))):
                raise ValueError(f"{name}: '{key}' must be an object name, not a $reference")
        self.table = AbsorberTable(config.get("absorber_factors", (1,)))
        self.autoabsorber = AutoAbsorber(self, config.get("autoabsorber", {}) or {})
        rois = {k: (int(v[0]), int(v[1])) for k, v in (config.get("rois", {}) or {}).items()}
        for roi in ("spec", "offspec1", "offspec2"):
            if roi not in rois:
                raise ValueError(f"{name}: rois must define {roi} (spec ROI_SPECULAR / OFFSPEC1 / OFFSPEC2)")
        self.roi_channels = rois
        masked = config.get("masked_channels", []) or []
        if not all(isinstance(c, int) and 0 <= c for c in masked):
            raise ValueError(f"{name}: masked_channels must be a list of channel numbers")
        self.masked_channels = tuple(sorted(set(masked)))

        def roi(key):
            start, end = rois[key]
            return lambda s, dev: float(s[start:end].sum()) * dev.factor

        def spec_corr(s, dev):
            return roi("spec")(s, dev) - (roi("offspec1")(s, dev) + roi("offspec2")(s, dev)) / 2

        self._xrd_counters = [
            SpectrumCounter("peak", self, lambda s, dev: float(s.max())),
            SpectrumCounter("peak_corr", self, lambda s, dev: float(s.max()) * dev.factor),
            SpectrumCounter("spec", self, roi("spec")),
            SpectrumCounter("offspec1", self, roi("offspec1")),
            SpectrumCounter("offspec2", self, roi("offspec2")),
            SpectrumCounter("spec_corr", self, spec_corr),
            SpectrumCounter("sum", self, lambda s, dev: float(s.sum())),
            # The detector's exposure is set to the count time in prepare();
            # the pre-count restores it before each count.
            SpectrumCounter("exposure", self, lambda s, dev: float(dev.count_time)),
            SpectrumCounter("abs", self, lambda s, dev: dev.absorber_position),
        ]

    # -- counters & acquisition --------------------------------------------

    @property
    def counters(self):
        return counter_namespace([self._spectrum] + self._xrd_counters)

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        trigger_mode = acq_params.pop("trigger_mode", None)
        if isinstance(trigger_mode, str):
            acq_params["trigger_type"] = (
                AcquisitionSlave.SOFTWARE if trigger_mode.upper() == "SOFTWARE" else AcquisitionSlave.HARDWARE
            )
        return XrdMythenSlave(self, ctrl_params=ctrl_params, **acq_params)

    # -- helpers used by the acquisition and the autoabsorber --------------

    def masked(self, spectrum) -> numpy.ndarray:
        """The spectrum with ``masked_channels`` interpolated from the nearest
        unmasked channels (a copy; the input is not changed)."""
        spectrum = numpy.asarray(spectrum)
        if not self.masked_channels:
            return spectrum
        channels = numpy.arange(len(spectrum))
        bad = numpy.isin(channels, self.masked_channels)
        out = spectrum.astype(float)
        out[bad] = numpy.interp(channels[bad], channels[~bad], out[~bad])
        return out

    def absorber(self):
        from bliss.config.static import get_config

        if not self.absorber_name:
            raise RuntimeError(f"{self.name}: no absorber configured")
        return get_config().get(self.absorber_name)

    def precount(self, count_time: float):
        """One short acquisition outside the scan's own frames; the scan's
        exposure time and frame count are restored afterwards."""
        iface = self._interface
        exposure, frames = iface.get_exposure_time(), iface.get_nframes()
        try:
            iface.set_nframes(1)
            iface.set_exposure_time(count_time)
            iface.start()
            try:
                gevent.sleep(count_time)
            finally:
                iface.stop()
            return iface.readout(1)[0]
        finally:
            iface.set_exposure_time(exposure)
            iface.set_nframes(frames)

    def close_shutter_for_rate(self, peak: float, position: int):
        message = (f"{self.name}: count rate too high ({peak:g} counts in "
                   f"{self.autoabsorber.precount_time:g} s with {self.absorber_name} at {position})")
        if not self.shutter_name:
            raise CountRateTooHigh(message + "; NO SHUTTER CONFIGURED")
        from bliss.config.static import get_config

        try:
            get_config().get(self.shutter_name).close()
        except Exception as exc:
            raise CountRateTooHigh(f"{message}; closing {self.shutter_name} FAILED: {exc}") from exc
        raise CountRateTooHigh(f"{message}; {self.shutter_name} closed")


class XrdMythenSlave(MythenAcquistionSlave):
    """Runs the autoabsorber check before each count and records the absorber
    position/factor used by the counters."""

    def __init__(self, *args, trigger_type=AcquisitionSlave.HARDWARE, **kwargs):
        super().__init__(*args, trigger_type=trigger_type, **kwargs)
        self._software = trigger_type == AcquisitionSlave.SOFTWARE
        self.factor = 1.0
        self.absorber_position = float("nan")

    # (the base class calls register_device() on our SpectrumCounters)

    def trigger(self):
        device = self.device
        if device.absorber_name:
            if self._software:
                device.autoabsorber.check()
            else:
                log_info(device, "hardware-triggered acquisition: no autoabsorber pre-count")
            absorber = device.absorber()
            self.absorber_position = float(absorber.position)
            self.factor = device.table.factor(self.absorber_position)
        super().trigger()
