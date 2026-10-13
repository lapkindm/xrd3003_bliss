"""Fake PNOZmulti m1p on a pseudo-terminal: answers the table-3 read request.

Usage: python tests/fake_pnoz.py LINK
Test hooks: ASCII lines "SIM:closed", "SIM:door1", "SIM:both",
"SIM:inconsistent", "SIM:silent" (no reply). Requests are counted in LINK + ".log".
"""
import os
import sys
import termios
import tty

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from xrd3003_bliss.doors import REQUEST_TABLE_3  # noqa: E402

STATES = {"closed": 0x6D, "door1": 0x8D, "both": 0x91, "inconsistent": 0x4D, "silent": None}


def reply(i0_7):
    body = bytes([0xAF, 0, 0, 0, 3, 0, i0_7, 0x02] + [0] * 11)
    return b"\x05\x15\x00\x14" + body + bytes([(-sum(body)) & 0xFF, 0x10])


def main():
    link = sys.argv[1]
    master, slave = os.openpty()
    tty.setraw(slave)
    attrs = termios.tcgetattr(slave); attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(os.ttyname(slave), link)
    log = open(link + ".log", "w", buffering=1)
    state, buf = "closed", b""
    while True:
        buf += os.read(master, 1024)
        while True:
            if buf.startswith(b"SIM:") and b"\r" in buf:
                line, buf = buf.split(b"\r", 1)
                state = line[4:].decode()
                continue
            if len(buf) >= len(REQUEST_TABLE_3) and buf.startswith(REQUEST_TABLE_3[:4]):
                frame, buf = buf[:len(REQUEST_TABLE_3)], buf[len(REQUEST_TABLE_3):]
                log.write(("ok " if frame == REQUEST_TABLE_3 else "unexpected ") + frame.hex(" ") + "\n")
                if frame == REQUEST_TABLE_3 and STATES[state] is not None:
                    os.write(master, reply(STATES[state]))
                continue
            break


if __name__ == "__main__":
    main()
