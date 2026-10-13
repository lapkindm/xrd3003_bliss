"""dscan / d2scan / d3scan that put the absorber back afterwards, as spec's
redefined macros in site.mac do (``dscan_cleanup a2 olda2 ...``, also run
when the scan is aborted).

In the session setup::

    from xrd3003_bliss.scans import dscan, d2scan, d3scan
"""

from __future__ import annotations

import functools

from bliss.common import scans as _bliss_scans

ABSORBER = "a2"


def keeping_absorber(scan_func, absorber_name: str = ABSORBER):
    """Wrap a scan so the absorber returns to its start position at the end."""

    @functools.wraps(scan_func)
    def wrapper(*args, **kwargs):
        from bliss.config.static import get_config

        absorber = get_config().get(absorber_name)
        start = absorber.position
        try:
            return scan_func(*args, **kwargs)
        finally:
            if absorber.position != start:
                print(f"Returning {absorber.name} to {start:g}")
                absorber.move(start)

    return wrapper


dscan = keeping_absorber(_bliss_scans.dscan)
d2scan = keeping_absorber(_bliss_scans.d2scan)
d3scan = keeping_absorber(_bliss_scans.d3scan)
