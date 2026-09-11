#!/usr/bin/env python3
"""Guards and probes for the #177 rollback: old code + czsc 0.10.12.

The rollback target is a frozen commit, not a moving branch. After PR #179
lands, ``origin/main`` is czsc 1.0.1; pointing this script at current main
or the upgrade tree must fail.

This helper lives on the 1.0.1 / upgrade tree. Run it with the *restore*
interpreter, and pass the old checkout via ``--old-code-root``. Do not
activate ``~/.venvs/czsc`` (that env is 1.0.1).

Examples::

    # Pre-install pin check (no czsc import required):
    python spikes/0009-czsc-1.0.1-upgrade/acceptance/verify_rollback.py \\
        --old-code-root /path/to/stockpilot-wt-01012 --check-pin-only

    # Frozen-sample checksums + version probe (restore venv active):
    source ~/.venvs/czsc-rollback/bin/activate
    python spikes/0009-czsc-1.0.1-upgrade/acceptance/verify_rollback.py \\
        --old-code-root /path/to/stockpilot-wt-01012 --probe-versions
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

VERIFIED_OLD_SHA = "2883f34e956e49372f69c379b0ef45f8e63c929c"
EXPECTED_PYPROJECT_PIN = "czsc==0.10.12"
EXPECTED_ENGINE_VERSION = "0.10.12"

_PINNED_VERSION_RE = re.compile(
    r'^PINNED_ENGINE_VERSION\s*=\s*"([^"]+)"',
    re.MULTILINE,
)


def _fail(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git rev-parse failed in {root}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def read_pyproject(root: Path) -> str:
    path = root / "pyproject.toml"
    if not path.is_file():
        raise FileNotFoundError(f"missing pyproject.toml: {path}")
    return path.read_text(encoding="utf-8")


def read_pinned_engine_version(root: Path) -> str:
    path = root / "packages" / "chantheory" / "config.py"
    if not path.is_file():
        raise FileNotFoundError(f"missing chantheory config: {path}")
    match = _PINNED_VERSION_RE.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"PINNED_ENGINE_VERSION not found in {path}")
    return match.group(1)


def is_formal_venv() -> bool:
    """True when this process is the formal ~/.venvs/czsc env (czsc 1.0.1).

    Do not Path.resolve() the python *binary*: venv wrappers are symlinks to
    the base interpreter, so resolve() would miss the venv.
    """
    formal = Path.home() / ".venvs" / "czsc"
    try:
        formal_resolved = formal.resolve()
    except FileNotFoundError:
        formal_resolved = formal
    for raw in (sys.prefix, sys.exec_prefix):
        try:
            if Path(raw).resolve() == formal_resolved:
                return True
        except FileNotFoundError:
            continue
    abs_exec = os.path.abspath(sys.executable)
    formal_bin = str(formal / "bin") + os.sep
    return abs_exec.startswith(formal_bin) or abs_exec.startswith(
        str(formal_resolved / "bin") + os.sep
    )


def refuse_formal_venv() -> str | None:
    if not is_formal_venv():
        return None
    return (
        f"interpreter is the formal venv ({sys.executable}, prefix={sys.prefix}); "
        "that environment is czsc 1.0.1. Use the restore venv instead."
    )


def check_old_checkout(root: Path, expected_sha: str) -> list[str]:
    errors: list[str] = []
    try:
        head = git_head(root)
    except RuntimeError as exc:
        return [str(exc)]
    if head != expected_sha:
        errors.append(
            f"old-code HEAD is {head}, expected {expected_sha}. "
            "Do not use origin/main after the 1.0.1 merge; "
            "fix the checkout to the verified 0.10.12 commit."
        )
    try:
        pyproject = read_pyproject(root)
    except FileNotFoundError as exc:
        errors.append(str(exc))
        pyproject = ""
    if pyproject and EXPECTED_PYPROJECT_PIN not in pyproject:
        errors.append(
            f"{root / 'pyproject.toml'} does not declare {EXPECTED_PYPROJECT_PIN}. "
            "Installing .[dev] from this tree would pull czsc 1.0.1."
        )
    try:
        pinned = read_pinned_engine_version(root)
    except (FileNotFoundError, ValueError) as exc:
        errors.append(str(exc))
        pinned = ""
    if pinned and pinned != EXPECTED_ENGINE_VERSION:
        errors.append(
            f"PINNED_ENGINE_VERSION is {pinned!r}, expected {EXPECTED_ENGINE_VERSION!r}."
        )
    return errors


_PROBE_SOURCE = r"""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
expected = sys.argv[2]
# Match T0 (repo root on sys.path) and chan-viewer (packages/ on sys.path).
sys.path.insert(0, str(root / "packages"))
sys.path.insert(0, str(root))

import czsc
import chantheory
import packages.chantheory as pkg_ct
from packages.chantheory import analyze, analyze_multi_timeframe

def under_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False

errors = []
chantheory_file = Path(chantheory.__file__).resolve()
pkg_file = Path(pkg_ct.__file__).resolve()
expected_pkg = (root / "packages" / "chantheory" / "__init__.py").resolve()
if not under_root(chantheory_file):
    errors.append(f"chantheory loaded from {chantheory_file}, not under {root}")
if pkg_file != expected_pkg:
    errors.append(f"packages.chantheory loaded from {pkg_file}, expected {expected_pkg}")
if czsc.__version__ != expected:
    errors.append(f"czsc.__version__={czsc.__version__!r} != {expected!r}")
if chantheory.PINNED_ENGINE_VERSION != expected:
    errors.append(
        f"chantheory.PINNED_ENGINE_VERSION={chantheory.PINNED_ENGINE_VERSION!r} != {expected!r}"
    )
if pkg_ct.PINNED_ENGINE_VERSION != expected:
    errors.append(
        f"packages.chantheory.PINNED_ENGINE_VERSION={pkg_ct.PINNED_ENGINE_VERSION!r} != {expected!r}"
    )

inputs = root / "spikes" / "0009-czsc-1.0.1-upgrade" / "baseline" / "inputs"

def load(name: str):
    return json.loads((inputs / name).read_text(encoding="utf-8"))

rows_5m = load("5m_real_600584_548_rows.json")
rows_30m = load("30m_600584_sh_rows.json")
single_5m = analyze(rows=rows_5m, symbol="600584.SH", timeframe="5m", source="tencent")
single_30m = analyze(rows=rows_30m, symbol="600584.SH", timeframe="30m", source="tencent")
multi = analyze_multi_timeframe(
    rows_by_timeframe={"5m": rows_5m, "30m": rows_30m},
    symbol="600584.SH",
    base_timeframe="5m",
    source="tencent",
)
level_versions = {level.timeframe: level.analysis.engine_version for level in multi.levels}
for label, version in (
    ("analyze_5m", single_5m.engine_version),
    ("analyze_30m", single_30m.engine_version),
    ("analyze_multi_timeframe", multi.engine_version),
    *[(f"multi_level_{tf}", ver) for tf, ver in level_versions.items()],
):
    if version != expected:
        errors.append(f"{label} engine_version={version!r} != {expected!r}")

payload = {
    "ok": not errors,
    "python": sys.executable,
    "czsc": czsc.__version__,
    "pinned_engine_version": chantheory.PINNED_ENGINE_VERSION,
    "chantheory_file": str(chantheory_file),
    "packages_chantheory_file": str(pkg_file),
    "analyze_5m": single_5m.engine_version,
    "analyze_30m": single_30m.engine_version,
    "analyze_multi_timeframe": multi.engine_version,
    "multi_level_engine_versions": level_versions,
    "errors": errors,
}
print(json.dumps(payload, indent=2, ensure_ascii=False))
raise SystemExit(0 if not errors else 1)
"""


def probe_versions(root: Path) -> int:
    return subprocess.call(
        [sys.executable, "-c", _PROBE_SOURCE, str(root), EXPECTED_ENGINE_VERSION],
        cwd=str(root),
    )


def verify_baseline(root: Path) -> int:
    script = root / "spikes" / "0009-czsc-1.0.1-upgrade" / "baseline" / "generate_baseline.py"
    if not script.is_file():
        return _fail(f"missing old baseline generator: {script}")
    print(f"script={script}")
    return subprocess.call([sys.executable, str(script), "--verify"], cwd=str(root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--old-code-root",
        type=Path,
        required=True,
        help="Checkout pinned at the verified 0.10.12 SHA (not origin/main after merge).",
    )
    parser.add_argument(
        "--expected-sha",
        default=VERIFIED_OLD_SHA,
        help=f"Verified pre-upgrade commit (default {VERIFIED_OLD_SHA}).",
    )
    parser.add_argument(
        "--check-pin-only",
        action="store_true",
        help="Only verify SHA + pyproject pin + PINNED_ENGINE_VERSION; do not import czsc.",
    )
    parser.add_argument(
        "--probe-versions",
        action="store_true",
        help="Run analyze / analyze_multi_timeframe and assert engine_version=0.10.12.",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip generate_baseline.py --verify (still runs pin check).",
    )
    args = parser.parse_args(argv)

    root = args.old_code_root.resolve()
    print(f"python={sys.executable}")
    print(f"old_code_root={root}")
    print(f"expected_sha={args.expected_sha}")

    errors = check_old_checkout(root, args.expected_sha)
    if errors:
        return _fail("old-checkout pin check failed:\n- " + "\n- ".join(errors))
    print(f"pin_ok={EXPECTED_PYPROJECT_PIN} sha={args.expected_sha}")

    if args.check_pin_only:
        return 0

    formal = refuse_formal_venv()
    if formal:
        return _fail(formal)

    status = 0
    if not args.skip_verify:
        status = verify_baseline(root)
        if status:
            return status
    if args.probe_versions:
        status = probe_versions(root)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
