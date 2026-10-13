"""Autoabsorber logic of the XRD-3003TT, ported from spec's site.mac.

spec (``/usr/local/lib/spec.d/site.mac``, Cu tube):

- ``absorber_korrektur``: with the absorber wheel ``a2`` at position p
  (1 = no absorber, 6 = thickest), counts are multiplied by
  ``absob[1] * ... * absob[p-1]`` with ``absob = (1, 7.97, 7.59, 7.7, 7.79, 7.26)``.
- ``check_rate`` (run before every count via ``user_precount``): count 0.1 s,
  take the peak (``max`` of the Mythen spectrum), then

  * peak > 70000                     -> close the shutter, abort
  * autoabsorber off                 -> done
  * peak > 12400 and a2 at 6         -> close the shutter, abort
  * peak > 12400                     -> a2 + 1, count again
  * peak < 1550 and a2 above 1       -> a2 - 1, count again
  * otherwise                        -> done

No I/O here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

SPEC_CU_FACTORS = (1, 7.97, 7.59, 7.7, 7.79, 7.26)


class AbsorberError(RuntimeError):
    """The absorber is not at a valid position."""


class AbsorberTable:
    """Correction factors of the absorber wheel positions."""

    def __init__(self, factors=SPEC_CU_FACTORS, first_position: int = 1, tolerance: float = 0.01):
        self.factors = tuple(float(f) for f in factors)
        if not self.factors or any(f <= 0 for f in self.factors):
            raise ValueError("absorber factors must be positive")
        self.first = int(first_position)
        self.tolerance = tolerance

    @property
    def last(self) -> int:
        return self.first + len(self.factors) - 1

    def position(self, raw: float) -> int:
        """Integer wheel position of an axis position, or AbsorberError."""
        p = round(raw)
        if abs(raw - p) > self.tolerance or not self.first <= p <= self.last:
            raise AbsorberError(
                f"absorber at {raw:g}, not at one of the positions {self.first}..{self.last}"
            )
        return p

    def factor(self, raw: float) -> float:
        """Correction factor at a position: product of absob[1..p-1] (spec)."""
        p = self.position(raw)
        result = 1.0
        for f in self.factors[1:p - self.first + 1]:
            result *= f
        return result


class Action(Enum):
    OK = "ok"
    MORE = "more absorber"
    LESS = "less absorber"
    CLOSE = "close shutter"


@dataclass(frozen=True)
class Thresholds:
    """Peak counts in one pre-count (spec: 0.1 s)."""

    close_above: float = 70000
    upper: float = 12400
    lower: float = 1550


def decide(peak: float, position: int, table: AbsorberTable, thresholds: Thresholds, enabled: bool) -> Action:
    """One step of spec's check_rate."""
    if peak > thresholds.close_above:
        return Action.CLOSE
    if not enabled:
        return Action.OK
    if peak > thresholds.upper:
        return Action.CLOSE if position >= table.last else Action.MORE
    if peak < thresholds.lower and position > table.first:
        return Action.LESS
    return Action.OK
