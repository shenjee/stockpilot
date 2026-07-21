"""Regenerate the committed ADR 0008 fixture."""

from __future__ import annotations

import argparse

from fixture import FIXTURE_PATH, canonical_fixture_bytes, fixture_sha256, generate_fixture, validate_fixture


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify committed bytes instead of writing")
    args = parser.parse_args()
    payload = generate_fixture()
    errors = validate_fixture(payload)
    if errors:
        raise SystemExit("fixture validation failed: " + "; ".join(errors))
    expected = canonical_fixture_bytes(payload)
    if args.check:
        actual = FIXTURE_PATH.read_bytes()
        if actual != expected:
            raise SystemExit("committed fixture differs from deterministic generator")
    else:
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_bytes(expected)
    print(f"{FIXTURE_PATH}: sha256={fixture_sha256(payload)} bars={len(payload['bars'])}")


if __name__ == "__main__":
    main()
