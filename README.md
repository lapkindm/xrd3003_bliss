# xrd3003-bliss

Bliss site package of the **XRD-3003TT** diffractometer (GE ISO-DEBYEFLEX
3003 generator, Mythen 1K detector, ViX and Trinamic motors). It ports the
detector part of spec's `site.mac` and adds what the instrument needed when
it moved from spec to Bliss.

| Module | What it does |
|---|---|
| `mythen` | the Mythen 1K with spec's counters, the autoabsorber and the count-rate protection |
| `absorber` | the absorber correction table and spec's `check_rate` decisions |
| `scans` | `dscan`, `d2scan`, `d3scan` that put the absorber back afterwards |
| `doors` | the door state from the Pilz PNOZmulti safety relay (read-only) |
| `interlock` | stops a scan cleanly when a door opens or the beam goes off |
| `scan_commands` | analysis of the last scan: `deriv`, `deriv_fit`, `deriv_fwhm`, `step_fit`, `goto_step`, `shad_fit` |
| `actions` | `with_actions`: run commands at given points of a scan |
| `saving` | `newfile`: change the data file with spec-like scan numbers |
| `notify` | e-mail when a long measurement finishes or stops on an error |
| `flint_display` | keeps Flint on top and on its live tab during scans |

| spec | Bliss |
|---|---|
| `user_getcounts`, `absorber_korrektur` | `mythen` counters `peak peak_corr spec offspec1 offspec2 spec_corr sum abs exposure` (spec: `det dcr spc of1 of2 scr dsm abs`) |
| `user_precount` → `check_rate` | pre-count before every Mythen count (scans and `ct`) |
| `auto_on` / `auto_off` | `auto_on()` / `auto_off()` (session script) |
| `dscan`, `d2scan`, `d3scan` with `dscan_cleanup a2` | `xrd3003_bliss.scans` (session script) |
| `newfile` | `newfile(experimenter, sample)` |
| `PEAK`, `CEN` on an edge | `step_fit()`, `goto_step()` |

Configs: [bliss_config/xrd3003tt_mythen.yml](bliss_config/xrd3003tt_mythen.yml),
[bliss_config/xrd3003tt_doors.yml](bliss_config/xrd3003tt_doors.yml).
Install with `pip install -e .` in the Bliss environment. The modules are
made available in a session by its setup and session script, e.g.
`from xrd3003_bliss.scan_commands import step_fit, goto_step`.

## Counters

f is the absorber correction factor at the current `a2` position: the
product `absob[1]·…·absob[p-1]`, with spec's `absob` table for Cu.

| counter | spec name | value |
|---|---|---|
| `peak` | `det` | peak (max) of the spectrum |
| `peak_corr` | `dcr` | `peak` · f |
| `spec`, `offspec1`, `offspec2` | `spc`, `of1`, `of2` | ROI sums · f |
| `spec_corr` | `scr` | `spec − (offspec1 + offspec2) / 2` |
| `sum` | `dsm` | sum of the spectrum |
| `abs` | `abs` | absorber position |
| `exposure` | | exposure time of the count in s, for normalisation (the autoabsorber pre-count is not included) |
| `spectrum` | | the full 1280-channel spectrum |

The ROIs are in the YAML as `[start, end)`, with the end channel excluded.
spec's ROI ranges included the end channel.

`masked_channels` (1271 and 1273 on this detector, which give noise bursts)
are replaced by linear interpolation from the nearest good channels before
all counters and the pre-count peak are computed. The saved `spectrum` stays
raw.

## Autoabsorber and count-rate protection (spec `check_rate`)

Before each count, after the motors have reached the point, the controller
takes a 0.1 s pre-count and looks at the peak:

| peak in the 0.1 s pre-count | action |
|---|---|
| above 70000 | close the shutter and abort, even with the autoabsorber off |
| above 12400, `a2` at 6 | close the shutter and abort |
| above 12400 | `a2` + 1, then count again |
| below 1550, `a2` above 1 | `a2` − 1, then count again |
| otherwise | count |

Differences from spec:

- **Bounded loop.** At most `max_steps` absorber changes per point (spec loops
  until it settles), then a warning.
- **In-between positions are refused.** An `a2` between wheel positions is
  refused, because its correction factor would be meaningless.
- **Exposure is restored.** The pre-count restores the detector's exposure
  time and frame count, so the real count is unaffected.

`a2` must be an integer position 1..6. The thresholds are peak counts in the
pre-count, as in spec.

## Interlock during scans

`xrd3003_bliss.interlock.InterlockPreset` sits on the default acquisition
chain of the `xrd` session. It watches scans that start with the shutter
open. At each point and at the end of the scan, it checks that both doors
read closed (PNOZ), the shutter is still open and HV is still on. If not, it
sends `CS`, so the shutter cannot reopen by itself, and aborts the scan with
the reasons, e.g. `scan aborted (interlock): door1 open, shutter Closed
(closed unexpectedly!), high voltage off`.

The generator's hardware interlock acts first and remains the safety. The
preset only stops the scan cleanly. The cost is about 90 ms per point on the
instrument.

## Doors

`doors.PnozDoors` reads the inputs of the PNOZmulti m1p safety relay over its
RS232 interface (19200 baud, 8E2) with one read-only request, table 3
"state of inputs", and never writes to the relay. Each door has "closed"
inputs (two channels) and an "open" input; it counts as closed only if all
of them agree. `doors.state()` gives e.g. `{"door1": "closed", "door2":
"open"}`. The shutter of `isovolt_driver` uses it (`door_check: doors`) to
refuse an open before the generator ever sees the attempt.

## Analysing the last scan

These work like Bliss's `cen()`: on the last scan and the `plotselect`
counter unless given, one result per scanned motor (a mapping for an
`a2scan`), and they draw their result in a Flint tab of their own.

| Command | Gives |
|---|---|
| `deriv()` | three-point derivative dy/dx at each measured point (`numpy.gradient`) |
| `deriv_fit()`, `deriv_fwhm()` | Gaussian fit of the derivative (Flint's own fit); the FWHM is corrected for the widening by the finite step |
| `step_fit()`, `goto_step()` | error-function step fit: edge position, beam FWHM, both levels, with errors; `goto_step` moves there |
| `shad_fit()` | shadow scan: highest point, crossing of the two fitted flank lines (`apex`) and their signed slopes |

`step_fit` leaves out a reflection peak after the edge (it fits the dark side
only until the signal climbs again) and refuses data without a clear step.
On 168 recorded z scans its edge agreed with spec's `CEN` to 1 µm (median).
`shad_fit` gives every point to the side of the apex it lies on, so an exact
triangle gives exact slopes (spec counted the highest point on both sides).

## Actions during a scan

```python
with_actions(loopscan(100, 1, run=False), {10: lamp_on, 20: "umv(temp, 100)"})
```

`n: action` runs after n points have been measured and before the motors move
on; the scan waits for it. An action is a function, a `(function, arg, ...)`
tuple or a command text run in the session. Everything is checked before the
scan starts, each action is written as a note into the scan's data, and a
failing action stops the scan.

## Data files

`newfile("alice", "PEN_4P")` sets `SCAN_SAVING.experimenter` and `.sample`
and continues the scan numbers of that file, starting at 1 for a new file.
Bliss itself numbers scans per session, not per file.

## E-mail notifications

```python
with notifying("spatial scan -25..25", to="a@example.org") as status:
    ...                                   # status["point"] = "12/51" is reported
```

One e-mail when the block finishes and one when it stops on an error, sent
through the PC's own `sendmail`; nothing on Ctrl-C, also inside a scan. Error
e-mails also always go to `notify.ERRORS_ALWAYS_TO`, which the instrument's
session sets. A mail problem never stops the measurement.

## Flint

`flint_display.FlintOnTopPreset` (on the default chain) switches Flint to
"always on top" and back to its live tab at the start of each scan. GNOME on
Wayland does not let Flint bring itself to the front, but it does honour
"always on top". The preset checks the real window state (`xprop`) and
re-applies it when the desktop has dropped it. `flint_on_top(False)` in the
session switches it off.

## Tests

`tests/run_e2e.sh` runs `tests/bliss_e2e.py` against simulated hardware: a
throwaway Beacon, a fake Mythen (`tests/fake_mythen.py`, UDP 1030 / TCP 1031
on 127.0.0.1) whose beam depends on mockup motors, the fake generator from
`isovolt_driver` (expected next to this repository) and a fake PNOZ
(`tests/fake_pnoz.py`). It covers the counters, the pre-count, the
autoabsorber, both shutter-close cases, `a2` restore after `dscan`, the doors
and the interlock, the analysis commands, `with_actions`, `newfile` and the
notifications (60 checks).

The other tests need neither Bliss sessions nor hardware:

- `tests/test_absorber.py` checks the factors and decisions against literal
  transcriptions of spec's `absorber_korrektur` and `check_rate`, over 132
  beam / position / on-off combinations.
- `tests/test_doors.py` decodes captured replies of the real relay.
- `tests/test_scan_commands.py` runs the derivative, step and shadow fits on
  synthetic edges and triangles with known widths, slopes and noise.
- `tests/test_notify.py`, `tests/test_saving.py` and
  `tests/test_flint_display.py` cover the e-mails, `newfile` and the Flint
  preset with fakes.
