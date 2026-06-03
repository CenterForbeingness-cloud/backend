#!/usr/bin/env python3
"""
Create an admin user or rotate TOTP for an existing admin.

Run from the backend directory:

  python scripts/create_admin.py create --email you@company.com --role owner
  python scripts/create_admin.py totp --email you@company.com

Requires SUPABASE_DB_URL (and loads backend/.env via dotenv).
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow `from app.*` when invoked as `python scripts/create_admin.py`
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_BACKEND_ROOT, ".env"))


def _cmd_create(args: argparse.Namespace) -> int:
    from app.admin_auth import create_admin_user

    if len(args.password) < 8:
        print("Password must be at least 8 characters", file=sys.stderr)
        return 1

    admin_id, err = create_admin_user(args.email, args.password, args.role)
    if err or not admin_id:
        print(f"Failed: {err}", file=sys.stderr)
        return 1
    print(f"Created admin: {args.email} (role={args.role})")
    print("Next: python scripts/create_admin.py totp --email", args.email)
    return 0


def _cmd_totp(args: argparse.Namespace) -> int:
    import pyotp

    from app.admin_auth import generate_totp_secret, set_totp_secret
    from app.config import ADMIN_2FA_ISSUER

    secret = generate_totp_secret()
    ok, err = set_totp_secret(args.email, secret)
    if not ok:
        print(f"Failed: {err}", file=sys.stderr)
        return 1

    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=args.email, issuer_name=ADMIN_2FA_ISSUER)
    print(f"TOTP enabled for {args.email}")
    print(f"Secret (store offline): {secret}")
    print(f"Provisioning URI (QR): {uri}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sentient admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    create_p = sub.add_parser("create", help="Create a new admin_users row")
    create_p.add_argument("--email", required=True)
    create_p.add_argument("--password", required=True)
    create_p.add_argument(
        "--role",
        default="owner",
        choices=["owner", "editor", "viewer"],
    )

    totp_p = sub.add_parser("totp", help="Generate and store a new TOTP secret")
    totp_p.add_argument("--email", required=True)

    args = parser.parse_args()
    if args.command == "create":
        return _cmd_create(args)
    if args.command == "totp":
        return _cmd_totp(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
