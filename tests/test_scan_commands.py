"""derivative() and fit_derivative() on synthetic edges with a known width."""

import math
import unittest

import numpy

from xrd3003_bliss.scan_commands import DerivativeFit, derivative, fit_derivative, fit_shadow, fit_step

K = 2 * math.sqrt(2 * math.log(2))  # FWHM / sigma


def edge(x, center, sigma, high, low, falling=True):
    """A beam cut by an edge: erf step from high to low (or low to high)."""
    s = 0.5 * numpy.vectorize(math.erfc)((x - center) / (sigma * math.sqrt(2)))
    return low + (high - low) * (s if falling else 1 - s)


class Derivative(unittest.TestCase):
    def test_exact_for_a_quadratic_with_uneven_steps(self):
        x = numpy.array([0, 0.1, 0.25, 0.3, 0.5, 0.7])
        xx, d = derivative(x, 3 * x ** 2 + 2 * x + 1)
        self.assertTrue(numpy.allclose(xx, x))
        self.assertTrue(numpy.allclose(d, 6 * x + 2))

    def test_refuses_too_few_or_repeated_points(self):
        for x in ([0, 1], [0, 1, 1, 2]):
            with self.assertRaises(ValueError):
                derivative(x, numpy.arange(len(x)))

    def test_aborted_scan_with_one_more_position(self):
        xx, d = derivative([0, 1, 2, 3], [0, 2, 4])
        self.assertEqual(len(xx), 3)


class Fit(unittest.TestCase):
    x = numpy.linspace(5.7, 8.7, 51)  # like z_scan: dscan z -1.5 1.5 50

    def test_falling_edge(self):  # z scan into the sample
        y = edge(self.x, 7.19, 0.05, 2.8e5, 20)
        c, w, h, b, wf = fit_derivative(self.x, y)
        self.assertAlmostEqual(c, 7.19, delta=0.002)
        # step 0.06 > sigma 0.05: the step correction is approximate there (~3 %)
        self.assertAlmostEqual(w, K * 0.05, delta=0.05 * K * 0.05)
        self.assertLess(h, 0)

    def test_rising_edge(self):
        y = edge(self.x, 7.0, 0.08, 1e4, 0, falling=False)
        c, w, h, b, wf = fit_derivative(self.x, y)
        self.assertAlmostEqual(c, 7.0, delta=0.002)
        self.assertAlmostEqual(w, K * 0.08, delta=0.01 * K * 0.08 + 0.002)
        self.assertGreater(h, 0)

    def test_noisy_edge_with_uneven_steps(self):
        rng = numpy.random.default_rng(1)
        x = numpy.sort(self.x + rng.uniform(-0.01, 0.01, self.x.size))
        y = rng.poisson(edge(x, 7.2, 0.06, 2e5, 50)).astype(float)
        c, w, h, b, wf = fit_derivative(x, y)
        self.assertAlmostEqual(c, 7.2, delta=0.01)
        self.assertAlmostEqual(w, K * 0.06, delta=0.1 * K * 0.06)

    def test_fit_curve(self):
        f = DerivativeFit(1.0, 0.0, -5.0, 0.5, K * 0.1, "spec", "z", None, None)
        self.assertAlmostEqual(float(f.curve(1.0)), -4.5)
        self.assertAlmostEqual(float(f.curve(1.0 + K * 0.1 / 2)), 0.5 - 2.5)

    def test_uncorrected_width_is_wider(self):
        y = edge(self.x, 7.19, 0.05, 2.8e5, 20)
        c, w, h, b, wf = fit_derivative(self.x, y)
        self.assertGreater(wf, w * 1.05)  # step 0.06 vs sigma 0.05: ~ +20 %

    def test_edge_sharper_than_the_step_is_refused(self):
        with self.assertRaises(RuntimeError):
            fit_derivative(self.x, edge(self.x, 7.19, 0.005, 2.8e5, 20))

    def test_flat_data_fails_clearly(self):
        with self.assertRaises(RuntimeError):
            fit_derivative(self.x, numpy.full(self.x.size, 100.0))


def triangle(x, apex, height, left, right, floor=0.0):
    """Shadow scan: lines rising with slope left, falling with slope right
    (negative), cut at a floor."""
    y = numpy.where(x <= apex, height + left * (x - apex), height + right * (x - apex))
    return numpy.maximum(y, floor)


class Shadow(unittest.TestCase):
    x = numpy.linspace(-0.5, 0.5, 41)  # like shad_scan: a2scan th -0.5 0.5 40

    def test_exact_triangle(self):
        y = triangle(self.x, 0.031, 8e4, 2.5e5, -1.5e5, floor=-numpy.inf)  # no baseline
        peak, apex, h, ml, mr, bl, br, nl, nr = fit_shadow(self.x, y)
        self.assertAlmostEqual(ml, 2.5e5, delta=1)
        self.assertAlmostEqual(mr, -1.5e5, delta=1)
        self.assertAlmostEqual(apex, 0.031, delta=1e-6)
        self.assertAlmostEqual(h, 8e4, delta=1)
        self.assertAlmostEqual(peak, 0.025)  # nearest scan point below the apex
        self.assertEqual(nl + nr, len(self.x))  # every point on exactly one side

    def test_scan_in_decreasing_order(self):  # e.g. tth of an a2scan th -0.5 0.5 tth 0.5 -0.5
        y = triangle(self.x, -0.02, 5e4, 2e5, -2e5, floor=-numpy.inf)
        a = fit_shadow(self.x, y)
        b = fit_shadow(self.x[::-1], y[::-1])
        self.assertTrue(numpy.allclose(a, b))

    def test_baseline_biases_whole_flank_fit_levels_fix_it(self):
        y = triangle(self.x, 0.0, 1e5, 4e5, -4e5, floor=50)  # flat at 50 beyond +-0.25
        whole = fit_shadow(self.x, y)
        cut = fit_shadow(self.x, y, levels=(0.1, 1.0))
        self.assertLess(whole[3], 0.8 * 4e5)  # flat points flatten the line
        self.assertAlmostEqual(cut[3], 4e5, delta=1)
        self.assertAlmostEqual(cut[4], -4e5, delta=1)

    def test_noisy(self):
        rng = numpy.random.default_rng(2)
        y = rng.poisson(triangle(self.x, 0.04, 3e4, 1e5, -0.8e5, floor=0)).astype(float)
        peak, apex, h, ml, mr, *_ = fit_shadow(self.x, y, levels=(0.1, 0.95))
        self.assertAlmostEqual(apex, 0.04, delta=0.01)
        self.assertAlmostEqual(ml, 1e5, delta=0.05e5)
        self.assertAlmostEqual(mr, -0.8e5, delta=0.05e5)

    def test_maximum_at_the_end_is_refused(self):
        with self.assertRaises(ValueError):
            fit_shadow(self.x, self.x + 1)  # rising only

    def test_bad_levels(self):
        with self.assertRaises(ValueError):
            fit_shadow(self.x, triangle(self.x, 0, 1, 1, -1), levels=(0.9, 0.1))


class Step(unittest.TestCase):
    x = numpy.linspace(5.7, 8.7, 51)  # like z_scan: 0.06 mm steps

    def test_falling_edge(self):
        y = edge(self.x, 7.19, 0.05, 2.8e5, 20)
        pos, w, hi, lo, pe, we, falling, x0, x1 = fit_step(self.x, y)
        self.assertTrue(falling)
        self.assertAlmostEqual(pos, 7.19, delta=1e-4)
        self.assertAlmostEqual(w, K * 0.05, delta=1e-3)
        self.assertAlmostEqual(hi, 2.8e5, delta=10)
        self.assertAlmostEqual(lo, 20, delta=10)

    def test_reflection_peak_after_the_edge_is_left_out(self):
        y = edge(self.x, 7.19, 0.05, 2.8e5, 20) + 1.4e5 * numpy.exp(-0.5 * ((self.x - 7.86) / 0.05) ** 2)
        pos, w, hi, lo, pe, we, falling, x0, x1 = fit_step(self.x, y)
        self.assertLess(x1, 7.7)  # fit range ends before the peak
        self.assertAlmostEqual(pos, 7.19, delta=1e-4)
        self.assertAlmostEqual(w, K * 0.05, delta=1e-3)

    def test_rising_edge(self):
        y = edge(self.x, 7.0, 0.08, 1e4, 30, falling=False)
        pos, w, hi, lo, pe, we, falling, *_ = fit_step(self.x, y)
        self.assertFalse(falling)
        self.assertAlmostEqual(pos, 7.0, delta=1e-4)
        self.assertAlmostEqual(w, K * 0.08, delta=1e-3)

    def test_noisy_edge_gives_errors(self):
        rng = numpy.random.default_rng(3)
        y = rng.poisson(edge(self.x, 7.2, 0.06, 2e5, 50)).astype(float)
        pos, w, hi, lo, pe, we, *_ = fit_step(self.x, y)
        self.assertAlmostEqual(pos, 7.2, delta=5 * pe + 1e-4)
        self.assertAlmostEqual(w, K * 0.06, delta=5 * we + 1e-3)
        self.assertTrue(0 < pe < 0.01 and 0 < we < 0.05)

    def test_explicit_range(self):
        y = edge(self.x, 7.19, 0.05, 2.8e5, 20)
        *_, x0, x1 = fit_step(self.x, y, xrange=(6.5, 7.6))
        self.assertGreaterEqual(x0, 6.5)
        self.assertLessEqual(x1, 7.6)

    def test_flat_data_fails_clearly(self):
        with self.assertRaises(RuntimeError):
            fit_step(self.x, numpy.full(self.x.size, 100.0))

    def test_noise_without_an_edge_is_refused(self):  # spec 261007 #S17: aborted on the plateau
        rng = numpy.random.default_rng(4)
        y = 2.79e5 + rng.normal(0, 2000, 16)
        with self.assertRaises(RuntimeError):
            fit_step(self.x[:16], y)


if __name__ == "__main__":
    unittest.main()
