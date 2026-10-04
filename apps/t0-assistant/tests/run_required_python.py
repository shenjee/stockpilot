"""Run one required unittest target; empty or entirely skipped suites fail."""

from __future__ import annotations

import argparse
import platform
import sys
import unittest
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start_directory")
    parser.add_argument("pattern", nargs="?", default="test_*.py")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    print(f"Python {platform.python_version()} ({sys.executable}), {platform.platform()}", flush=True)
    print(f"Required target: {args.start_directory}/{args.pattern}", flush=True)
    loader = unittest.TestLoader()
    suite = loader.discover(args.start_directory, pattern=args.pattern)
    count = suite.countTestCases()
    print(f"Discovered tests: {count}", flush=True)
    if count == 0 or loader.errors:
        for error in loader.errors:
            print(error, file=sys.stderr)
        print("Required target has no tests or discovery errors", file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    executed = result.testsRun - len(result.skipped)
    print(f"Required target: discovered={count}, run={result.testsRun}, skipped={len(result.skipped)}", flush=True)
    return 0 if result.wasSuccessful() and executed > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
