"""Run the DUDE test suite.

Usage:
    python run_tests.py            # run all tests
    python run_tests.py -v         # verbose
    python run_tests.py <pattern>  # run only matching tests (e.g. intent)

Uses the standard-library unittest runner, so no third-party install is needed.
"""

import os
import sys
import unittest


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv or "--verbose" in sys.argv

    suite = unittest.defaultTestLoader.discover(
        start_dir=os.path.join(root, "tests"), pattern="test*.py")

    if args:
        # Filter to tests whose id/name contains one of the given patterns.
        needles = [a.lower() for a in args]
        selected = unittest.TestSuite()
        for test in suite:
            for sub in test:
                rid = str(sub).lower()
                if any(n in rid for n in needles):
                    selected.addTest(sub)
        suite = selected

    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())