"""Headless unit tests for Windows naming constraints (Phase 5, P10/P11).

Pure validation logic: no desktop, no filesystem writes.
"""
import unittest

from core.orchestrator.naming import (
    split_destination,
    validate_directory,
    validate_filename,
)


class TestValidateFilename(unittest.TestCase):
    def test_plain_name_ok(self):
        ok, reason = validate_filename("P4_NoteA.txt")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_reserved_device_name_rejected(self):
        for bad in ("AUX", "AUX.txt", "con", "NUL", "COM1", "lpt9"):
            ok, reason = validate_filename(bad)
            self.assertFalse(ok, bad)
            self.assertIn("reserved", reason)

    def test_illegal_characters_rejected(self):
        for bad in ("a/b.txt", "a?b.txt", "a*b.txt", 'a"b.txt',
                    "a<b.txt", "a|b.txt", "a:b.txt"):
            ok, reason = validate_filename(bad)
            self.assertFalse(ok, bad)
            self.assertIn("illegal", reason)

    def test_empty_and_blank_rejected(self):
        for bad in ("", "   ", None):
            ok, _ = validate_filename(bad)
            self.assertFalse(ok, repr(bad))

    def test_dot_tail_rejected(self):
        ok, reason = validate_filename("name.")
        self.assertFalse(ok)
        self.assertIn("dots or spaces", reason)


class TestSplitDestination(unittest.TestCase):
    def test_full_path_splits(self):
        directory, name = split_destination(
            r"C:\Users\duvvu\Desktop\D\P4_NoteA.txt")
        self.assertEqual(directory, r"C:\Users\duvvu\Desktop\D")
        self.assertEqual(name, "P4_NoteA.txt")

    def test_bare_name_has_no_directory(self):
        directory, name = split_destination("P4_NoteA.txt")
        self.assertEqual(directory, "")
        self.assertEqual(name, "P4_NoteA.txt")

    def test_empty(self):
        self.assertEqual(split_destination(""), ("", ""))


class TestValidateDirectory(unittest.TestCase):
    def test_absolute_ok(self):
        ok, _ = validate_directory(r"C:\Users\duvvu\Desktop\D")
        self.assertTrue(ok)

    def test_relative_rejected(self):
        ok, reason = validate_directory(r"Desktop\D")
        self.assertFalse(ok)
        self.assertIn("absolute", reason)

    def test_empty_rejected(self):
        ok, _ = validate_directory("")
        self.assertFalse(ok)


if __name__ == '__main__':
    unittest.main()
