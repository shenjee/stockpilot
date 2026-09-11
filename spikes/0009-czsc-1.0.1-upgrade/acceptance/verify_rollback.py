#!/usr/bin/env python3
"""Run the #177 rollback drill: old code + czsc 0.10.12 + frozen samples.

This script must be executed with the *old* checkout on sys.path (a 0.10.12
pin, typically main / worktree ``stockpilot-wt-01012``). It refuses to run
against czsc 1.0.1. Passing the current upgrade-branch tree is incorrect:
new adapter code would rewrite provenance and fail the checksum compare.

Example:

    source ~/.venvs/czsc-rollback-drill-177/bin/activate
    python /path/to/old-checkout/spikes/0009-czsc-1.0.1-upgrade/baseline/generate_baseline.py --verify
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--old-code-root",
        type=Path,
        required=True,
        help="Checkout whose pyproject still pins czsc==0.10.12 (e.g. main worktree).",
    )
    args = parser.parse_args()
    root = args.old_code_root.resolve()
    script = root / "spikes" / "0009-czsc-1.0.1-upgrade" / "baseline" / "generate_baseline.py"
    if not script.is_file():
        print(f"missing old baseline generator: {script}", file=sys.stderr)
        return 2
    print(f"python={sys.executable}")
    print(f"old_code_root={root}")
    print(f"script={script}")
    return subprocess.call([sys.executable, str(script), "--verify"], cwd=str(root))


if __name__ == "__main__":
    raise SystemExit(main())
