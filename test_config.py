#!/usr/bin/env python3
"""Quick test to verify config is set up correctly."""
import os
import sys

# Load config
from config import ENV_TYPE, BOT_TOKEN_DEV, BOT_TOKEN_LIVE, MODE

print("=" * 50)
print("CONFIG CHECK")
print("=" * 50)
print(f"ENV_TYPE: {ENV_TYPE}")
print(f"MODE: {MODE}")
print(f"BOT_TOKEN_DEV: {'[SET]' if BOT_TOKEN_DEV else '[NOT SET]'}")
print(f"BOT_TOKEN_LIVE: {'[SET]' if BOT_TOKEN_LIVE else '[NOT SET]'}")

if ENV_TYPE == 'LOCAL':
    if not BOT_TOKEN_DEV:
        print("\n[ERROR] BOT_TOKEN_DEV is required for LOCAL mode!")
        sys.exit(1)
    print("\n[OK] LOCAL mode configured correctly")
elif ENV_TYPE == 'VPS':
    if not BOT_TOKEN_LIVE:
        print("\n[ERROR] BOT_TOKEN_LIVE is required for VPS mode!")
        sys.exit(1)
    print("\n[OK] VPS mode configured correctly")
else:
    print(f"\n[WARNING] Unknown ENV_TYPE: {ENV_TYPE}")

print("=" * 50)

