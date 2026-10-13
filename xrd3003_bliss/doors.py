"""Door state of the XRD-3003TT from the Pilz PNOZmulti m1p safety relay.

Read-only use of the PNOZmulti's integrated RS232 interface (*PNOZmulti
communication interfaces*, Pilz 1001154-EN-13, ch. 5 and appendix 9.3;
``isovolt_driver/docs/pnoz_com.txt``): request 0x2F "send data in table form",
table 3 "state of inputs", segment 0. Nothing is ever written to the relay
(the write requests 0x14/0x53 are not implemented).

Wiring found on 2026-10-06 (both doors closed: I0-I7 = 0x6D):

    door 1: I5, I6 high when closed (two channels), I7 high when open
    door 2: I2, I3 high when closed (two channels), I4 high when open

Door 2 can only be opened after door 1. A door counts as closed only if all
its "closed" inputs are 1 and all its "open" inputs are 0; anything else
(open, channels disagreeing, no valid reply) is not closed. Example YAML::

    - plugin: bliss
      package: xrd3003_bliss.doors
      class: PnozDoors
      name: doors
      serial:
        url: /dev/ttyS6          # 19200 baud, 8 data bits, even parity, 2 stop bits
      doors:
        door1: {closed: [5, 6], open: [7]}
        door2: {closed: [2, 3], open: [4]}
"""

from __future__ import annotations

import time

import gevent.lock

REQUEST_TABLE_3 = bytes([0x05, 0x15, 0x00, 0x07, 0x2F, 0x00, 0x00, 0x00, 0x03, 0x00, 0xCE, 0x10])
REPLY_LEN = 25


class DoorError(RuntimeError):
    """A door is open, or the door state cannot be read."""


def parse_inputs(reply: bytes) -> list[int]:
    """Input bits I0..I19 of the base unit from a table-3 segment-0 reply."""
    if len(reply) != REPLY_LEN:
        raise DoorError(f"PNOZ reply has {len(reply)} bytes, expected {REPLY_LEN}: {reply.hex(' ')}")
    if reply[:3] != b"\x05\x15\x00" or reply[4] != 0xAF or reply[8] != 3 or reply[9] != 0 or reply[24] != 0x10:
        raise DoorError(f"unexpected PNOZ reply: {reply.hex(' ')}")
    if (-sum(reply[4:23])) & 0xFF != reply[23]:
        raise DoorError(f"PNOZ reply checksum error: {reply.hex(' ')}")
    data = reply[10:13]
    return [data[i // 8] >> (i % 8) & 1 for i in range(20)]


def door_state(bits: list[int], closed: list[int], open_: list[int]) -> str:
    """'closed', 'open' or 'inconsistent' (channels disagree)."""
    if all(bits[i] == 1 for i in closed) and all(bits[i] == 0 for i in open_):
        return "closed"
    if all(bits[i] == 0 for i in closed) and all(bits[i] == 1 for i in open_):
        return "open"
    return "inconsistent"


class PnozDoors:
    def __init__(self, name, config):
        self.name = name
        self._config = config
        serial_cfg = config.get("serial", {}) or {}
        self._url = str(serial_cfg.get("url", "/dev/ttyS6"))
        self._doors = {
            door: ([int(i) for i in spec.get("closed", [])], [int(i) for i in spec.get("open", [])])
            for door, spec in (config.get("doors", {}) or {}).items()
        }
        if not self._doors:
            raise ValueError(f"{name}: no doors configured")
        self._timeout = float(config.get("timeout", 0.5))
        self._port = None  # opened on first use
        self._lock = gevent.lock.RLock()

    @property
    def config(self):
        return self._config

    def _serial(self):
        import serial

        if self._port is None:
            self._port = serial.Serial(self._url, 19200, bytesize=8, parity=serial.PARITY_EVEN,
                                       stopbits=2, timeout=0.05)
        return self._port

    def inputs(self) -> list[int]:
        """Read I0..I19 of the base unit (one read-only request)."""
        with self._lock:
            port = self._serial()
            port.reset_input_buffer()
            port.write(REQUEST_TABLE_3)
            reply, deadline = b"", time.monotonic() + self._timeout
            while len(reply) < REPLY_LEN and time.monotonic() < deadline:
                reply += port.read(REPLY_LEN - len(reply))
        if not reply:
            raise DoorError(f"{self.name}: no reply from the PNOZ on {self._url}")
        return parse_inputs(reply)

    def state(self) -> dict[str, str]:
        bits = self.inputs()
        return {door: door_state(bits, closed, open_) for door, (closed, open_) in self._doors.items()}

    def all_closed(self) -> bool:
        try:
            return all(s == "closed" for s in self.state().values())
        except DoorError:
            return False

    def check(self) -> None:
        """Raise DoorError unless every door is reported closed."""
        try:
            states = self.state()
        except DoorError as exc:
            raise DoorError(f"{self.name}: door state unknown ({exc})") from exc
        bad = {d: s for d, s in states.items() if s != "closed"}
        if bad:
            raise DoorError(f"{self.name}: " + ", ".join(f"{d} {s}" for d, s in bad.items()))

    def __info__(self):
        try:
            bits = self.inputs()
        except DoorError as exc:
            return f"{self.name}: door state unknown: {exc}"
        lines = [f"{self.name} (PNOZ m1p on {self._url}):"]
        for door, (closed, open_) in self._doors.items():
            lines.append(f"  {door}: {door_state(bits, closed, open_).upper()}   "
                         f"(closed inputs {closed} = {[bits[i] for i in closed]}, open inputs {open_} = "
                         f"{[bits[i] for i in open_]})")
        return "\n".join(lines)
