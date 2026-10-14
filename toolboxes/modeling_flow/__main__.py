"""One bounded JSON result on stdout; diagnostics stay on stderr."""
from __future__ import annotations

import argparse
import contextlib
import sys

from .contracts import canonical_bytes
from .runner import generate, health


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Mac-local image-to-shape runner")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("health")
    check.add_argument("--config", required=True)
    run = commands.add_parser("generate")
    run.add_argument("--config", required=True)
    run.add_argument("--request", required=True)
    run.add_argument("--output", required=True)
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        result = (health(args.config) if args.command == "health" else
                  generate(args.config, args.request, args.output))
    sys.stdout.buffer.write(canonical_bytes(result) + b"\n")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
