import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from xrd3003_bliss.doors import REQUEST_TABLE_3, DoorError, door_state, parse_inputs  # noqa: E402

# Replies captured from the real PNOZ m1p on 2026-10-06
CLOSED = bytes.fromhex("05 15 00 14 af 00 00 00 03 00 6d 02 00 00 00 00 00 00 00 00 00 00 00 df 10")


def reply(i0_7, i8_15=0x02):
    body = bytes([0xAF, 0, 0, 0, 3, 0, i0_7, i8_15] + [0] * 11)
    return b"\x05\x15\x00\x14" + body + bytes([(-sum(body)) & 0xFF, 0x10])


DOORS = {"door1": ([5, 6], [7]), "door2": ([2, 3], [4])}


def states(r):
    bits = parse_inputs(r)
    return {d: door_state(bits, c, o) for d, (c, o) in DOORS.items()}


class Pnoz(unittest.TestCase):
    def test_request_is_the_documented_read(self):
        self.assertEqual(REQUEST_TABLE_3[4], 0x2F)          # "send data in table form"
        self.assertEqual(REQUEST_TABLE_3[8:10], b"\x03\x00")  # table 3, segment 0
        self.assertEqual((-sum(REQUEST_TABLE_3[4:10])) & 0xFF, REQUEST_TABLE_3[10])

    def test_captured_replies(self):
        self.assertEqual(reply(0x6D), CLOSED)  # helper builds the same bytes as the real unit
        self.assertEqual(states(CLOSED), {"door1": "closed", "door2": "closed"})
        self.assertEqual(states(reply(0x8D)), {"door1": "open", "door2": "closed"})   # door 1 open
        self.assertEqual(states(reply(0x91)), {"door1": "open", "door2": "open"})     # both open

    def test_disagreeing_channels(self):
        self.assertEqual(states(reply(0x4D))["door1"], "inconsistent")  # I5 dropped, I6 still high
        self.assertEqual(states(reply(0xED))["door1"], "inconsistent")  # closed and "open" input high

    def test_bad_replies(self):
        bad = bytearray(CLOSED); bad[23] ^= 1
        for r in (bytes(bad), CLOSED[:20], b""):
            with self.assertRaises(DoorError):
                parse_inputs(r)


if __name__ == "__main__":
    unittest.main()
