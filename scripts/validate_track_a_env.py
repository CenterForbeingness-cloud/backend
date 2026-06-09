#!/usr/bin/env python3
"""
Validate Track A launch gates from backend/.env or Railway env.

Usage (from backend/):
  python scripts/validate_track_a_env.py
  python scripts/validate_track_a_env.py --strict-live
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.launch_gates import evaluate_launch_gates  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Track A launch gate validator")
    parser.add_argument(
        "--strict-live",
        action="store_true",
        help="Require STRIPE_SECRET_KEY to be sk_live_...",
    )
    args = parser.parse_args()

    result = evaluate_launch_gates(strict_live=args.strict_live)
    checks = result.get("checks") or []

    print("Track A launch gates\n")
    for row in checks:
        mark = "OK " if row["ok"] else "FAIL"
        req = "required" if row.get("required", True) else "optional"
        print(f"  [{mark}] {row['gate_id']} {row['name']} ({req}) — {row['detail']}")

    blocking = result.get("blocking") or []
    print()
    if result.get("ready"):
        print("READY: all required gates pass.")
        if result.get("stripe_mode") == "test":
            print("Note: Stripe is in test mode. Use --strict-live before public launch.")
        sys.exit(0)

    print("NOT READY. Fix on Railway (or backend/.env for local):")
    for item in blocking:
        print(f"  - {item}")
    sys.exit(1)


if __name__ == "__main__":
    main()
