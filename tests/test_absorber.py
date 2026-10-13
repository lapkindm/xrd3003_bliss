import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from xrd3003_bliss.absorber import (  # noqa: E402
    SPEC_CU_FACTORS, AbsorberError, AbsorberTable, Action, Thresholds, decide,
)


def spec_factor(a2):
    """absorber_korrektur from site.mac, literally."""
    absob = SPEC_CU_FACTORS
    correction_factor, i = 1, 1
    while a2 - i:
        if a2 < 1:
            break
        correction_factor = absob[i] * correction_factor
        i += 1
    return correction_factor


def spec_check_rate(rate, a2, enabled=True):
    """check_rate from site.mac, literally; rate(a2) = peak of a 0.1 s count.
    Returns (final a2, closed)."""
    for _ in range(50):
        det = rate(a2)
        if det > 70000:
            return a2, True
        if not enabled:
            break
        if det > 12400 and a2 == 6:
            return a2, True
        if det > 12400 and a2 < 6:
            a2 += 1
            continue
        if 1550 < det <= 12400:
            break
        if det < 1550 and a2 != 1:
            a2 -= 1
            continue
        if det <= 1550 and a2 == 1:
            break
    return a2, False


def ours(rate, a2, enabled=True):
    table, t = AbsorberTable(), Thresholds()
    for _ in range(50):
        action = decide(rate(a2), a2, table, t, enabled)
        if action is Action.OK:
            return a2, False
        if action is Action.CLOSE:
            return a2, True
        a2 += 1 if action is Action.MORE else -1
    return a2, False


class Factors(unittest.TestCase):
    def test_matches_spec(self):
        table = AbsorberTable()
        for p in range(1, 7):
            self.assertAlmostEqual(table.factor(p), spec_factor(p))
        self.assertEqual(table.factor(1), 1.0)
        self.assertAlmostEqual(table.factor(2), 7.97)

    def test_positions(self):
        table = AbsorberTable()
        self.assertEqual(table.position(3.004), 3)
        for bad in (2.5, 0, 7, 3.05):
            with self.assertRaises(AbsorberError):
                table.position(bad)


class CheckRate(unittest.TestCase):
    def test_rules(self):
        table, t = AbsorberTable(), Thresholds()
        self.assertIs(decide(80000, 6, table, t, True), Action.CLOSE)
        self.assertIs(decide(80000, 3, table, t, False), Action.CLOSE)   # protection even when off
        self.assertIs(decide(20000, 6, table, t, True), Action.CLOSE)
        self.assertIs(decide(20000, 3, table, t, True), Action.MORE)
        self.assertIs(decide(20000, 3, table, t, False), Action.OK)
        self.assertIs(decide(1000, 3, table, t, True), Action.LESS)
        self.assertIs(decide(1000, 1, table, t, True), Action.OK)
        self.assertIs(decide(5000, 3, table, t, True), Action.OK)

    def test_same_outcome_as_spec(self):
        """Simulated beam: the peak of a 0.1 s count is i0 / factor(a2)."""
        table = AbsorberTable()
        for i0 in (100, 2000, 1e4, 5e4, 2e5, 1e6, 5e6, 3e7, 2e8, 5e8, 1e9):
            for start in range(1, 7):
                for enabled in (True, False):
                    def rate(a2):
                        return i0 / table.factor(a2)
                    with self.subTest(i0=i0, start=start, enabled=enabled):
                        self.assertEqual(ours(rate, start, enabled), spec_check_rate(rate, start, enabled))


if __name__ == "__main__":
    unittest.main()
