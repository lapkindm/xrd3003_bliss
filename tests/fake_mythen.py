"""Fake Mythen 1K (firmware 3.x protocol) as gevent servers, for tests.

UDP commands on <host>:1030, readout on TCP <host>:1031, as used by
bliss.controllers.mca.mythen. Every command answers an int32 (0 = OK) unless
it is one of the getters below. The spectrum is a Gaussian peak at channel
664 on a flat background; its height is ``beam(exposure)`` counts, given by
the test (so it can depend on the absorber position).
"""

import struct

import numpy
from gevent.server import DatagramServer, StreamServer

NCHANNELS = 1280


class FakeMythen:
    def __init__(self, beam, host="127.0.0.1"):
        self.beam = beam  # callable(exposure_s) -> peak counts
        self.exposure_100ns = 10_000_000
        self.frames = 1
        self.commands = []
        self.readouts = []  # exposure (s) of each readout
        self.hot = {}  # channel -> extra counts per readout (noisy channels)
        self.udp = DatagramServer((host, 1030), self._on_udp)
        self.tcp = StreamServer((host, 1031), self._on_tcp)

    def start(self):
        self.udp.start()
        self.tcp.start()

    def stop(self):
        self.udp.stop()
        self.tcp.stop()

    def _reply(self, command: str) -> bytes:
        self.commands.append(command)
        parts = command.split()
        if command == "-get version":
            return b"M3.1.0\0"
        if command == "-get nmodules":
            return struct.pack("<i", 1)
        if command == "-get time":
            return struct.pack("<q", self.exposure_100ns)
        if command == "-get frames":
            return struct.pack("<i", self.frames)
        if command == "-get nbits":
            return struct.pack("<i", 24)
        if parts[0] == "-time":
            self.exposure_100ns = int(parts[1])
        elif parts[0] == "-frames":
            self.frames = int(parts[1])
        return struct.pack("<i", 0)

    def _on_udp(self, data, address):
        self.udp.socket.sendto(self._reply(data.decode().strip()), address)

    def spectrum(self) -> numpy.ndarray:
        exposure = self.exposure_100ns / 1e7
        self.readouts.append(exposure)
        peak = self.beam(exposure)
        x = numpy.arange(NCHANNELS)
        counts = peak * numpy.exp(-0.5 * ((x - 664) / 2.0) ** 2) + 1
        for channel, extra in self.hot.items():
            counts[channel] += extra
        return numpy.round(counts).astype("<i4")

    def _on_tcp(self, sock, address):
        try:
            self._serve_tcp(sock, address)
        except BaseException as exc:
            print(f"[fake mythen] data handler ended by {exc!r}", flush=True)
            raise

    def _serve_tcp(self, sock, address):
        buf = b""
        while True:
            data = sock.recv(1024)
            if not data:
                return
            buf += data
            command = buf.decode(errors="replace").strip()
            buf = b""
            self.commands.append(command)
            if command.startswith("-readout"):
                sock.sendall(self.spectrum().tobytes())
            else:
                sock.sendall(struct.pack("<i", 0))
