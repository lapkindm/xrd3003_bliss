"""End-to-end test: XrdMythen (fake detector) + mockup a2 + IsovoltShutter
(fake generator), through real Bliss counts and scans.

Needs a fresh Beacon whose config is tests/e2e_config.yml with <LINK>
replaced by the fake generator link, the fake generator running
(isovolt_driver/tests/fake_generator.py <link>), UDP 1030 / TCP 1031 free on
127.0.0.1, and::

    BEACON_HOST=localhost:25000 PYTHONPATH=. python tests/bliss_e2e.py <link>
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from bliss.common.scans import ct, loopscan  # noqa: E402
from bliss.config.static import get_config  # noqa: E402

from fake_mythen import FakeMythen  # noqa: E402
from xrd3003_bliss.absorber import AbsorberTable  # noqa: E402
from xrd3003_bliss.mythen import CountRateTooHigh  # noqa: E402
from xrd3003_bliss.scans import dscan  # noqa: E402

LINK = sys.argv[1]
results = []
table = AbsorberTable()
cfg = get_config()
session = cfg.get("xrd_test")  # session with all objects (see the runner)
session.setup()
session.scan_saving.base_path = os.environ.get("XRD_TEST_DATA", "/tmp/xrd_test_data")
a2, mx = cfg.get("a2"), cfg.get("mx")

state = {"rate": 5e6}  # peak counts/s with no absorber


def beam(exposure):
    rate = state["rate"](mx.position) if callable(state["rate"]) else state["rate"]
    return rate * exposure / table.factor(round(a2.position))  # between positions: nearest


fake = FakeMythen(beam)
fake.start()


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def generator_moves():
    time.sleep(0.05)
    with open(LINK + ".log") as f:
        return [s for s in (line.strip() for line in f) if s[:3] in ("OS:", "CS:")]


def raises(fn):
    try:
        fn()
    except BaseException as e:  # noqa: BLE001
        return e
    return None


mythen = cfg.get("mythen")
shutter = cfg.get("shutter")
check("counters as in spec", sorted(c.name for c in mythen.counters)
      == sorted(["spectrum", "peak", "peak_corr", "spec", "offspec1", "offspec2", "spec_corr", "sum", "abs", "exposure"]),
      [c.name for c in mythen.counters])
mythen.autoabsorber.on()

# -- autoabsorber finds the right absorber -------------------------------------
a2.move(6)
s = ct(1, mythen)
v = s.get_data()
check("autoabsorber moved a2 6 -> 3 (spec rules)", a2.position == 3.0, a2.position)
f3 = table.factor(3)
check("peak = peak of the 1 s count at a2=3", abs(v["peak"][0] - round(5e6 / f3 + 1)) <= 1, v["peak"][0])
check("peak_corr = peak * factor(3)", abs(v["peak_corr"][0] - v["peak"][0] * f3) < 1e-6 * v["peak_corr"][0], v["peak_corr"][0])
check("abs counter", v["abs"][0] == 3.0, v["abs"][0])
check("exposure counter = count time (pre-count not included)", v["exposure"][0] == 1.0, v["exposure"][0])
check("pre-count did not change the 1 s exposure", fake.readouts[-1] == 1.0, fake.readouts[-4:])
roi = lambda a, b: float(v["spectrum"][0][a:b].sum())  # noqa: E731
check("spec/offspec1/offspec2 corrected ROI sums", abs(v["spec"][0] - roi(659, 670) * f3) < 1e-3
      and abs(v["offspec1"][0] - roi(647, 658) * f3) < 1e-3, (v["spec"][0], v["offspec1"][0]))
check("spec_corr = spec - (offspec1+offspec2)/2", abs(v["spec_corr"][0] - (v["spec"][0] - (v["offspec1"][0] + v["offspec2"][0]) / 2)) < 1e-6)
check("sum = raw sum", v["sum"][0] == float(v["spectrum"][0].sum()))

# -- masked (noisy) channels 1271, 1273 --------------------------------------------
fake.hot = {1271: 200000, 1273: 150000}  # bursts above close_above in a 0.1 s pre-count
err = raises(lambda: ct(1, mythen))
check("masked channels: no shutter close, a2 kept", err is None and a2.position == 3.0, (repr(err)[:80], a2.position))
v = ct(1, mythen).get_data()
raw = v["spectrum"][0]
check("raw spectrum keeps the noisy channels", raw[1271] > 100000 and raw[1273] > 100000, (raw[1271], raw[1273]))
check("peak ignores them", abs(v["peak"][0] - round(5e6 / f3 + 1)) <= 1, v["peak"][0])
clean = raw.astype(float)
clean[1271] = (raw[1270] + raw[1272]) / 2
clean[1273] = (raw[1272] + raw[1274]) / 2
check("sum = sum with 1271/1273 interpolated", abs(v["sum"][0] - clean.sum()) < 1e-6, (v["sum"][0], clean.sum()))
fake.hot = {}

# -- scan ------------------------------------------------------------------------
s = loopscan(3, 0.2, mythen)
check("loopscan: exposure 0.2 s at every point", list(s.get_data()["exposure"]) == [0.2] * 3, s.get_data()["exposure"])
check("loopscan: 3 points, absorber stays", len(s.get_data()["peak_corr"]) == 3 and a2.position == 3.0,
      s.get_data()["abs"])

# -- autoabsorber off: no absorber change, protection still on ------------------
mythen.autoabsorber.off()
state["rate"] = 2e5
ct(0.1, mythen)
check("autoabsorber off: a2 not moved on low rate", a2.position == 3.0, a2.position)

shutter.open()
check("shutter open (absorber to 6 first)", shutter.is_open and a2.position == 6.0)
a2.move(3)
state["rate"] = 5e8  # 0.1 s at a2=3: 826k counts
err = raises(lambda: ct(1, mythen))
check("rate > 70000 with autoabsorber off: CountRateTooHigh", isinstance(err, CountRateTooHigh)
      or "count rate too high" in str(err), str(err)[:120])
check("... and the shutter was closed", generator_moves()[-1] == "CS:1" and shutter.is_closed,
      generator_moves()[-2:])
mythen.autoabsorber.on()

# -- most absorber and still too much -------------------------------------------
shutter.open()
state["rate"] = 5.27e9  # about 20000 counts in 0.1 s at a2=6: above 12400, below 70000
a2.move(6)
err = raises(lambda: ct(1, mythen))
check("a2 at 6 and peak > 12400: shutter closed, aborted", err is not None and "count rate too high" in str(err)
      and shutter.is_closed, str(err)[:120])

# -- invalid absorber position ----------------------------------------------------
state["rate"] = 5e6
a2.move(3.5)
err = raises(lambda: ct(0.1, mythen))
check("absorber between positions: refused", err is not None and "not at one of the positions" in str(err),
      str(err)[:100])
a2.move(3)

# -- dscan puts a2 back ------------------------------------------------------------
state["rate"] = lambda x: 5e6 if x < 0.5 else 2e7  # second half: 33000 counts at a2=3 -> a2=4
mx.move(0)
start = a2.position
s = dscan(mx, 0, 1, 4, 0.1, mythen)
check("autoabsorber changed a2 during dscan", len(set(s.get_data()["abs"])) > 1, s.get_data()["abs"])
check("dscan returned a2 to its start", a2.position == start and mx.position == 0, (a2.position, mx.position))

shutter.open()
a2.move(3)
state["rate"] = lambda x: 5e6 if x < 0.5 else 5e7  # second half: 82600 counts at a2=3 -> close
err = raises(lambda: dscan(mx, 0, 1, 4, 0.1, mythen))
check("dscan aborted by the count rate, shutter closed", err is not None and "count rate too high" in str(err)
      and shutter.is_closed, str(err)[:100])
check("aborted dscan still returned a2", a2.position == 3.0 and mx.position == 0, (a2.position, mx.position))

# -- door interlock (PNOZ) -------------------------------------------------------
PLINK = sys.argv[2]


def pnoz(state):
    fd = os.open(PLINK, os.O_WRONLY | os.O_NOCTTY)
    os.write(fd, f"SIM:{state}\r".encode())
    os.close(fd)
    time.sleep(0.05)


doors = cfg.get("doors")
pnoz("closed")
check("doors object: both closed", doors.state() == {"door1": "closed", "door2": "closed"}, doors.state())
for pstate, expect in (("door1", "door1 open"), ("both", "door2 open"), ("inconsistent", "door1 inconsistent"),
                       ("silent", "door state unknown")):
    pnoz(pstate)
    a2.move(3)
    n = len(generator_moves())
    err = raises(shutter.open)
    check(f"PNOZ {pstate}: open refused, nothing moved or sent",
          type(err).__name__ == "InterlockError" and expect in str(err) and generator_moves()[n:] == []
          and a2.position == 3.0 and shutter.is_closed, (str(err)[:100], generator_moves()[n:], a2.position))
pnoz("closed")
check("doors closed again: open works", raises(shutter.open) is None and shutter.is_open, shutter.state_string)
shutter.close()
check("only read requests were sent to the PNOZ", all(l.startswith("ok ") for l in open(PLINK + ".log")),
      open(PLINK + ".log").read().splitlines()[-1:])

# -- interlock preset: abort scans when the interlock breaks -------------------
import gevent  # noqa: E402
from bliss.common.scans import DEFAULT_CHAIN  # noqa: E402
from xrd3003_bliss.interlock import InterlockAbort, InterlockPreset  # noqa: E402


def gen_sim(hook):
    fd = os.open(LINK, os.O_WRONLY | os.O_NOCTTY)
    os.write(fd, f"SIM:{hook}\r".encode())
    os.close(fd)
    time.sleep(0.05)


preset = InterlockPreset("shutter", "doors", "generator")
DEFAULT_CHAIN.add_preset(preset, name="interlock")
mythen.autoabsorber.off()
state["rate"] = 5e5
a2.move(4)

shutter.close()
pnoz("door1")
err = raises(lambda: loopscan(3, 0.1, mythen))
check("shutter closed, door open: scan not watched, runs", err is None and not preset.armed, repr(err)[:80])
pnoz("closed")

shutter.open()
preset.checks = 0
s = loopscan(5, 0.1, mythen)
check("shutter open, all fine: scan runs, checked every point", len(s.get_data()["peak"]) == 5 and preset.checks >= 5,
      (len(s.get_data()["peak"]), preset.checks))

n = len(generator_moves())
def trip_later():
    gevent.sleep(0.8)
    pnoz("door1")
    gen_sim("trip")
g = gevent.spawn(trip_later)
t0 = time.time()
err = raises(lambda: loopscan(20, 0.2, mythen))
g.join()
# The door and the generator trip are two steps of the simulation; the point
# check may fall between them and abort on the door alone (correctly).
check("interlock during a scan: aborted with the reasons", isinstance(err, InterlockAbort)
      and any(r in str(err) for r in ("door1 open", "closed unexpectedly", "high voltage off")),
      str(err)[:150])
check("... stopped early", time.time() - t0 < 20 * 0.2, f"{time.time() - t0:.1f} s")
check("... open command withdrawn (CS sent)", generator_moves()[n:] [-1:] == ["CS:1"], generator_moves()[n:])

gen_sim("reset")
pnoz("closed")
shutter.open()
s = loopscan(3, 0.1, mythen)
check("after reset: scans run again", len(s.get_data()["peak"]) == 3)
shutter.close()
DEFAULT_CHAIN.remove_preset(name="interlock")
mythen.autoabsorber.on()

# -- deriv(): spec-style derivative of the last scan --------------------------------
import numpy  # noqa: E402
from bliss.common.plot import plotselect  # noqa: E402
from xrd3003_bliss.scan_commands import deriv  # noqa: E402

shutter.close()
state["rate"] = lambda pos: 2e5 if pos < 0.5 else 4e4  # an edge in mx (below the 70000-count limit)
mythen.autoabsorber.off()
from bliss.common.scans import ascan  # noqa: E402
a2.move(1)
s = ascan(mx, 0, 1, 10, 0.05, mythen)
xs = numpy.asarray(s.streams["axis:mx"][:]); ys = numpy.asarray(s.streams["mythen:spec"][:])
d = deriv(mythen.counters.spec, plot=False)
inner = (ys[2:] - ys[:-2]) / (xs[2:] - xs[:-2])  # central differences (even steps here)
check("deriv: three-point derivative at the measured positions", numpy.allclose(d.x, xs)
      and numpy.allclose(d.y[1:-1], inner) and len(d.y) == len(ys) and d.axis == "mx" and d.counter == "spec",
      (d.counter, d.axis, len(d.y)))
check("deriv: the edge is the steepest point", 0.4 <= d.x[int(numpy.argmin(d.y))] <= 0.5, d.x[int(numpy.argmin(d.y))])
plotselect(mythen.counters.peak)
d2 = deriv(plot=False)
check("deriv(): uses the plotselect counter", d2.counter == "peak", d2.counter)
loopscan(4, 0.05, mythen)
d3 = deriv(mythen.counters.spec, plot=False)
check("deriv on a loopscan: against time (as cen())", "time" in d3.axis and len(d3.y) == 4, (d3.axis, len(d3.y)))

from xrd3003_bliss.scan_commands import deriv_fit, deriv_fwhm  # noqa: E402
import math  # noqa: E402
state["rate"] = lambda pos: 4e4 + 1.6e5 * 0.5 * math.erfc((pos - 0.52) / (0.08 * math.sqrt(2)))  # smooth edge
ascan(mx, 0, 1, 40, 0.05, mythen)
f = deriv_fit(mythen.counters.spec, plot=False)
true_fwhm = 2 * math.sqrt(2 * math.log(2)) * 0.08
check("deriv_fit: centre and FWHM of a smooth edge", abs(f.center - 0.52) < 0.005
      and abs(f.fwhm / true_fwhm - 1) < 0.03 and f.height < 0 and f.fwhm_fit > f.fwhm, (f.center, f.fwhm, true_fwhm))
w = deriv_fwhm(mythen.counters.spec, plot=False)
check("deriv_fwhm returns the FWHM", w == f.fwhm, w)

from xrd3003_bliss.scan_commands import shad_fit  # noqa: E402
state["rate"] = lambda pos: max(2e4, 1.2e5 - 3e5 * abs(pos - 0.43) * (1 if pos < 0.43 else 0.6))  # triangle
ascan(mx, 0, 1, 40, 0.05, mythen)
sf = shad_fit(mythen.counters.peak, plot=False, levels=(0.2, 1.0))
check("shad_fit: apex and flank slopes of a triangle", abs(sf.apex - 0.43) < 0.01 and sf.slope_left > 0 > sf.slope_right
      and abs(sf.slope_right / sf.slope_left + 0.6) < 0.05 and sf.axis == "mx", (sf.peak, sf.apex, sf.slope_left, sf.slope_right))
mythen.autoabsorber.on()
mx.move(0)

from xrd3003_bliss.scan_commands import step_fit  # noqa: E402
state["rate"] = lambda pos: 30 + 1.6e5 * 0.5 * math.erfc((pos - 0.47) / (0.06 * math.sqrt(2)))  # z-scan-like edge
ascan(mx, 0, 1, 50, 0.05, mythen)
st = step_fit(mythen.counters.spec, plot=False)
check("step_fit: edge position and beam FWHM", st.falling and abs(st.position - 0.47) < 0.002
      and abs(st.fwhm / (2 * math.sqrt(2 * math.log(2)) * 0.06) - 1) < 0.03, (st.position, st.fwhm, st.position_err))

from xrd3003_bliss.scan_commands import goto_step  # noqa: E402
mx.move(0)
g = goto_step(mythen.counters.spec, plot=False)
check("goto_step: motor moved to the fitted edge", abs(mx.position - g.position) < 2e-3 and abs(mx.position - 0.47) < 3e-3,
      (mx.position, g.position))
loopscan(5, 0.05, mythen)
pos_before = mx.position
err = raises(lambda: goto_step(mythen.counters.spec, plot=False))
check("goto_step after a loopscan: refused, nothing moved", err is not None and mx.position == pos_before, repr(err)[:100])

# -- notifying(): e-mail text from a real session (SMTP captured) -------------------
from unittest import mock as _mock  # noqa: E402
from xrd3003_bliss import notify as _nt  # noqa: E402
_mails = []
_smtp = _mock.MagicMock()
_smtp.return_value.__enter__.return_value.send_message.side_effect = lambda msg: _mails.append(msg.as_string())
with _mock.patch.object(_nt.smtplib, "SMTP", _smtp):
    with _nt.notifying("t@x.de", "e2e run") as _st:
        _st["point"] = "1/1"
        loopscan(2, 0.05, mythen)
    try:
        with _nt.notifying("t@x.de", "e2e fail"):
            raise RuntimeError("simulated stop")
    except RuntimeError:
        pass
check("notifying: finished and stopped e-mails with data file and last scan",
      len(_mails) == 2 and "Finished: e2e run" in _mails[0] and "Last scan: #" in _mails[0]
      and "Data file:" in _mails[0] and "STOPPED: e2e fail" in _mails[1] and "simulated stop" in _mails[1],
      [m[:80] for m in _mails])

# -- with_actions(): actions at given points of a scan ------------------------------
from xrd3003_bliss.actions import with_actions  # noqa: E402
state["rate"] = 2e4
mythen.autoabsorber.off()
a2.move(1)
log = []
session.env_dict["_actions_log"] = log
s = ascan(mx, 0, 1, 10, 0.05, mythen, run=False)
with_actions(s, {3: (lambda tag: log.append((tag, round(mx.position, 3))), "lamp"),
                 6: "_actions_log.append(('text', round(mx.position, 3)))"})
check("with_actions: each action after its point, before the next move",
      log == [("lamp", 0.2), ("text", 0.5)] and len(s.streams["axis:mx"][:]) == 11, log)
check("with_actions: recorded as scan comments",
      [c["message"] for c in s.comments] == ["after point 3: <lambda>('lamp')", "after point 6: _actions_log.append(('text', round(mx.position, 3)))"]
      or len(s.comments) == 2, [c["message"] for c in s.comments])
s = ascan(mx, 0, 1, 10, 0.05, mythen, run=False)
err = raises(lambda: with_actions(s, {4: lambda: 1 / 0}))
check("with_actions: a failing action stops the scan", isinstance(err, ZeroDivisionError) and len(s.streams["axis:mx"][:]) == 4,
      (repr(err)[:60], len(s.streams["axis:mx"][:])))
for bad, what in (({11: print}, "beyond the last point"), ({2: "umv(tmep, 100)"}, "unknown name"), ({2: 42}, "not a function")):
    s = ascan(mx, 0, 1, 10, 0.05, mythen, run=False)
    err = raises(lambda: with_actions(s, bad))
    check(f"with_actions refuses before the scan: {what}", err is not None and s.state == 0, repr(err)[:90])
# loop scans: each point = one pre-count + one count of the (fake) Mythen
from bliss.common.scans import timescan  # noqa: E402
seen = []
base = len(fake.readouts)
with_actions(loopscan(8, 0.05, mythen, run=False),
             {2: lambda: seen.append((len(fake.readouts) - base) // 2), 5: lambda: seen.append((len(fake.readouts) - base) // 2)})
check("with_actions on a loopscan: after 2 and after 5 counts", seen == [2, 5], seen)
s = timescan(0.05, mythen, run=False)  # endless
err = raises(lambda: with_actions(s, {3: lambda: (_ for _ in ()).throw(RuntimeError("enough"))}))
check("with_actions on an endless timescan: accepted, an action can end it",
      isinstance(err, RuntimeError) and "enough" in str(err) and len(s.streams["timer:elapsed_time"][:]) == 3, repr(err)[:80])
mx.move(0)
mythen.autoabsorber.on()

# -- newfile(): spec-like scan numbers per data file ---------------------------------
from xrd3003_bliss.saving import newfile  # noqa: E402

ss = session.scan_saving
ss.add("experimenter", "tester")
ss.add("sample", "s0")  # as in the xrd session setup
ss.template = "{experimenter}/"
ss.data_filename = "{sample}"
shutter.close()
mythen.autoabsorber.off()
a2.move(1)
nums = []
for exp, sample, n_scans in (("tester", "s1", 2), ("tester", "s2", 1), ("tester", "s1", 1), ("other", "s1", 1)):
    nums.append(("newfile", newfile(exp, sample)))
    for _ in range(n_scans):
        nums.append(int(loopscan(1, 0.05, mythen).scan_info["scan_nb"]))
check("newfile: numbers restart per file and continue in an existing one",
      nums == [("newfile", 1), 1, 2, ("newfile", 1), 1, ("newfile", 3), 3, ("newfile", 1), 1], nums)
check("newfile: the scans are in the right files",
      sorted(__import__("h5py").File(ss.filename.replace("other", "tester"), "r", locking=False).keys()) == ["1.1", "2.1", "3.1"],
      ss.filename)
mythen.autoabsorber.on()

fake.stop()
session.close()
print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
