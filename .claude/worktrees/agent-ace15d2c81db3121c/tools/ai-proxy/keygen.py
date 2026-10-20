#!/usr/bin/env python3
"""
Generate proxy API keys and add users to users.json.

Usage:
  python keygen.py <user_id> "<Full Name>" [--org m5x2] [--role member|admin]

Examples:
  python keygen.py jm "Jonathan McKay" --role admin
  python keygen.py ian "Ian Smith"
  python keygen.py matt "Matt Jones"
  python keygen.py lx "Lexi McKay" --role admin

After adding all users, set USERS_JSON on Fly.io:
  fly secrets set USERS_JSON="$(cat users.json | tr -d '\n')" -a m5x2-ai-proxy
"""

import argparse
import json
import secrets
import sys
from pathlib import Path

USERS_PATH = Path(__file__).parent / "users.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Add a user and generate their proxy API key")
    parser.add_argument("user_id", help="Short user code (e.g. ian, lx, matt)")
    parser.add_argument("name", help="Full name in quotes (e.g. 'Ian Smith')")
    parser.add_argument("--org", default="m5x2")
    parser.add_argument("--role", default="member", choices=["admin", "member"])
    args = parser.parse_args()

    users: dict = {}
    if USERS_PATH.exists():
        users = json.loads(USERS_PATH.read_text())

    # Remove existing entry for this user_id if present
    existing_key = next((k for k, v in users.items() if v.get("user_id") == args.user_id), None)
    if existing_key:
        print(f"User '{args.user_id}' already has a key. Regenerate? [y/N] ", end="", flush=True)
        if input().strip().lower() != "y":
            print("Aborted.")
            sys.exit(0)
        del users[existing_key]

    proxy_key = f"pk-{args.user_id}-{secrets.token_hex(16)}"
    users[proxy_key] = {
        "user_id": args.user_id,
        "name": args.name,
        "org": args.org,
        "role": args.role,
    }

    USERS_PATH.write_text(json.dumps(users, indent=2) + "\n")

    print(f"\nAdded: {args.name} ({args.user_id}) [{args.role}]")
    print(f"\nProxy key:\n  {proxy_key}")
    print(f"\nThey should add to their shell profile:")
    print(f'  export ANTHROPIC_API_KEY="{proxy_key}"')
    print(f'  export ANTHROPIC_BASE_URL="https://m5x2-ai-proxy.fly.dev"')
    print(f"\nTo push updated users to Fly.io:")
    print(f'  fly secrets set USERS_JSON="$(cat users.json | tr -d \'\\n\')" -a m5x2-ai-proxy')


if __name__ == "__main__":
    main()
