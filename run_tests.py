"""Run the maintained DUDE orchestrator tests (not live/quarantined scripts).

Usage:
    python run_tests.py            # run all tests
    python run_tests.py -v         # verbose
    python run_tests.py <pattern>  # run only matching tests (e.g. intent)

Uses the standard-library unittest runner, so no third-party install is needed.
"""

import argparse
from pathlib import Path
import sys
import unittest


def discover_tests(root=None, loader=None):
    """Load the maintained, flat test directory with collision-free module names.

    The tests directory is a namespace package (no __init__.py), which unittest
    discovery cannot use with an explicit top-level directory. Loading qualified
    names also avoids reusing an unrelated cached ``test_perception`` module.
    """
    root = Path(root or Path(__file__).resolve().parent).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    tests_dir = root / "core" / "orchestrator" / "tests"
    if not tests_dir.is_dir():
        raise FileNotFoundError(f"Maintained test directory not found: {tests_dir}")
    loader = loader if loader is not None else unittest.TestLoader()
    names = [
        f"core.orchestrator.tests.{path.stem}"
        for path in sorted(tests_dir.glob("test*.py"))
        if path.is_file()
    ]
    return loader.loadTestsFromNames(names)


def iter_tests(suite):
    """Yield individual cases from arbitrarily nested unittest suites."""
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from iter_tests(test)
        else:
            yield test


def filter_tests(suite, patterns):
    """Match module, class, or method names, case-insensitively (logical OR)."""
    needles = [pattern.lower() for pattern in patterns]
    return unittest.TestSuite(
        test for test in iter_tests(suite)
        if not needles or any(needle in test.id().lower() for needle in needles)
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("patterns", nargs="*", help="test name substrings (any match)")
    args = parser.parse_args(argv)

    root = str(Path(__file__).resolve().parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from _test_sandbox import runtime_sandbox

    # Isolation must be active during imports as well as test execution.
    with runtime_sandbox():
        return run_tests(args)


def run_tests(args):
    loader = unittest.TestLoader()
    suite = discover_tests(loader=loader)
    # A name filter must not silently hide an import/discovery failure.
    if loader.errors:
        for error in loader.errors:
            print(error, file=sys.stderr)
        return 1
    suite = filter_tests(suite, args.patterns)
    if not suite.countTestCases():
        print("No maintained tests matched the requested names.", file=sys.stderr)
        return 1

    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
