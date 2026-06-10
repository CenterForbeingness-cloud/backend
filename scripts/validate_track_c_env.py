#!/usr/bin/env python3
"""
Validate Track C voice + RAG gates from backend/.env or Railway env.

Usage (from backend/):
  python scripts/validate_track_c_env.py
  python scripts/validate_track_c_env.py --course week-zero-reset
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.voice_gates import evaluate_voice_gates  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Track C voice + RAG gate validator")
    parser.add_argument(
        "--course",
        default="week-zero-reset",
        help="Course slug for corpus/voice profile hints",
    )
    args = parser.parse_args()

    result = evaluate_voice_gates(course_slug=args.course)
    checks = result.get("checks") or []

    print("Track C voice + RAG gates\n")
    for row in checks:
        mark = "OK " if row["ok"] else "FAIL"
        req = "required" if row.get("required", True) else "optional"
        print(f"  [{mark}] {row['gate_id']} {row['name']} ({req}) — {row['detail']}")

    corpus = result.get("corpus") or {}
    print(
        f"\nCorpus ({args.course}): "
        f"{corpus.get('audio_files', 0)} audio file(s), "
        f"voice_configured={corpus.get('voice_configured', False)}"
    )

    blocking = result.get("blocking") or []
    print()
    if result.get("ready"):
        print("READY: all required gates pass.")
        if not result.get("voice_enabled"):
            print("Note: VOICE_ENABLED is false — voice endpoints return 503 until enabled.")
        sys.exit(0)

    print("NOT READY. Fix on Railway (or backend/.env for local):")
    for item in blocking:
        print(f"  - {item}")
    sys.exit(1)


if __name__ == "__main__":
    main()
