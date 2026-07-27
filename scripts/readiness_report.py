"""Run the deterministic M3 readiness fixture matrix."""

from __future__ import annotations

import argparse
import json

from browser_to_mcp.readiness import run_readiness


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        type=_positive_int,
        default=10,
        help="Number of times to execute each deterministic fixture.",
    )
    arguments = parser.parse_args()
    report = run_readiness(arguments.runs)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    raise SystemExit(0 if report.ready else 1)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


if __name__ == "__main__":
    main()
