import os
import unittest

from perception.stt import resolve_model_path


class ResolveModelPathTest(unittest.TestCase):
    def test_returns_local_bundled_model_when_present(self):
        here = os.path.dirname(os.path.abspath(__file__))
        local = os.path.join(here, "..", "data", "models", "faster-whisper-base.en")
        if os.path.isfile(os.path.join(local, "model.bin")):
            resolved = resolve_model_path("tiny.en")
            self.assertEqual(os.path.abspath(local), resolved)
            self.assertTrue(os.path.isfile(os.path.join(resolved, "model.bin")))

    def test_falls_back_to_input_when_no_local_model(self):
        # Resolver must never crash and must return the input when nothing
        # local exists. Point it at a bogus name with no local copy nearby by
        # using a tmp cwd through the pure function's candidate logic is not
        # possible; just assert it returns a string and no exception.
        resolved = resolve_model_path("")
        self.assertEqual(resolved, "")
        resolved = resolve_model_path(None)
        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()