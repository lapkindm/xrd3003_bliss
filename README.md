# xrd3003-bliss

XRD-3003TT site package for Bliss. It ports the detector part of spec's
`/usr/local/lib/spec.d/site.mac`:

| spec | Bliss |
|---|---|
| `user_getcounts`, `absorber_korrektur` | `mythen` counters `peak peak_corr spec offspec1 offspec2 spec_corr sum abs exposure` (spec: `det dcr spc of1 of2 scr dsm abs`) |
| `user_precount` → `check_rate` | pre-count before every Mythen count (scans and `ct`) |
| `auto_on` / `auto_off` | `auto_on()` / `auto_off()` (session script) |
| `dscan`, `d2scan`, `d3scan` with `dscan_cleanup a2` | `xrd3003_bliss.scans` (session script) |

Config: [bliss_config/xrd3003tt_mythen.yml](bliss_config/xrd3003tt_mythen.yml).
Install with `pip install -e .` in the Bliss environment.

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

## Tests

- **`tests/test_absorber.py`** checks the factors and the decisions against
  literal transcriptions of spec's `absorber_korrektur` and `check_rate`, over
  132 beam / position / on-off combinations.
- **`tests/bliss_e2e.py`** runs real Bliss `ct`, `loopscan` and `dscan` with
  three fakes: a fake Mythen (`tests/fake_mythen.py`, UDP 1030 / TCP 1031 on
  127.0.0.1) whose beam depends on the mockup `a2`, the fake generator from
  `isovolt_driver`, and a mockup `a2`. It covers the counters, the pre-count
  isolation, the autoabsorber, both shutter-close cases, aborts, and `a2`
  restore after normal and aborted `dscan`s.
