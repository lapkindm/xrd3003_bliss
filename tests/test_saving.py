"""newfile() with a fake SCAN_SAVING and real HDF5 files."""

import os
import tempfile
import unittest

import h5py

from xrd3003_bliss.saving import last_scan_number, newfile


class FakeScanSaving:
    def __init__(self, base):
        self.base, self.experimenter, self.sample, self.next = base, "x", "y", None

    @property
    def filename(self):
        return os.path.join(self.base, self.experimenter, f"{self.sample}.h5")

    def reset_scan_number(self, next=1):
        self.next = next


class NewFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ss = FakeScanSaving(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, experimenter, sample, entries):
        os.makedirs(os.path.join(self.tmp.name, experimenter), exist_ok=True)
        with h5py.File(os.path.join(self.tmp.name, experimenter, f"{sample}.h5"), "w") as f:
            for e in entries:
                f.create_group(e)

    def test_new_file_starts_at_1(self):
        self.assertEqual(newfile("Dima", "PEN_1", self.ss), 1)
        self.assertEqual((self.ss.experimenter, self.ss.sample, self.ss.next), ("Dima", "PEN_1", 1))

    def test_existing_file_continues_after_its_last_scan(self):
        self.make("Dima", "PEN_1", ["1.1", "2.1", "17.1", "17.2", "notes"])
        self.assertEqual(newfile("Dima", "PEN_1", self.ss), 18)
        self.assertEqual(self.ss.next, 18)

    def test_same_sample_other_experimenter_is_another_file(self):
        self.make("Dima", "PEN_1", ["5.1"])
        self.assertEqual(newfile("Janne", "PEN_1", self.ss), 1)

    def test_existing_file_without_scans(self):
        self.make("Dima", "empty", [])
        self.assertEqual(newfile("Dima", "empty", self.ss), 1)

    def test_unsafe_names_are_refused_before_anything_changes(self):
        for exp, sample in (("Dima", "PEN 1"), ("Dima", "../x"), ("", "a"), ("Dima", None)):
            with self.assertRaises(ValueError):
                newfile(exp, sample, self.ss)
        self.assertEqual((self.ss.experimenter, self.ss.sample, self.ss.next), ("x", "y", None))

    def test_missing_file(self):
        self.assertEqual(last_scan_number("/nonexistent/file.h5"), 0)


if __name__ == "__main__":
    unittest.main()
