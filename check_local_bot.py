"""
Diagnose why local bot does not respond to commands while VPS bot is active.
Run from project root: python check_local_bot.py
"""
import os
import sys

# Load .env like config does
def load_env(path='.env'):
    if path == '.env':
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, '.env')
    if not os.path.exists(path):
        return False
    with open(path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ[key.strip()] = value.strip().strip("'").strip('"')
    return True

if not load_env():
    print("[ERROR] .env not found. Create it from .env.example")
    sys.exit(1)

ENV_TYPE = os.getenv('ENV_TYPE', 'LOCAL').strip().upper()
BOT_DEV = os.getenv('BOT_TOKEN_DEV', '').strip()
BOT_LIVE = os.getenv('BOT_TOKEN_LIVE', '').strip()
MODE = os.getenv('MODE', 'DEV').strip().upper()

def mask(t):
    if not t or len(t) < 8:
        return "(empty or too short)"
    return f"{t[:4]}...{t[-4:]}"

print("=" * 50)
print("  LOCAL BOT DIAGNOSIS")
print("=" * 50)
print(f"  ENV_TYPE      : {ENV_TYPE}")
print(f"  MODE          : {MODE}")
print(f"  BOT_TOKEN_DEV : {'SET ' + mask(BOT_DEV) if BOT_DEV else 'NOT SET'}")
print(f"  BOT_TOKEN_LIVE: {'SET ' + mask(BOT_LIVE) if BOT_LIVE else 'NOT SET'}")
print("=" * 50)

issues = []
if not BOT_DEV:
    issues.append("BOT_TOKEN_DEV is not set in .env -> Local bot cannot start (or would exit with SECURITY TOKEN LOCK).")
elif BOT_LIVE and BOT_DEV == BOT_LIVE:
    issues.append("BOT_TOKEN_DEV and BOT_TOKEN_LIVE are THE SAME. Telegram delivers updates to only ONE running bot per token. So VPS gets all commands, local gets none.")
if ENV_TYPE == 'VPS':
    issues.append("ENV_TYPE=VPS on this machine -> This PC uses BOT_TOKEN_LIVE (same as server). Only one instance can receive updates; server wins.")

if issues:
    print("\n[PROBLEM]")
    for i, msg in enumerate(issues, 1):
        print(f"  {i}. {msg}")
    print("\n[FIX]")
    if not BOT_DEV:
        print("  - Add BOT_TOKEN_DEV to .env (create a second bot via @BotFather, e.g. SpiderDev).")
    if BOT_LIVE and BOT_DEV == BOT_LIVE:
        print("  - Set BOT_TOKEN_DEV to a DIFFERENT token (second bot from @BotFather). Keep BOT_TOKEN_LIVE for VPS only.")
    if ENV_TYPE == 'VPS':
        print("  - On this PC set ENV_TYPE=LOCAL in .env so local uses BOT_TOKEN_DEV.")
    print("  - Ensure MODE=DEV and ENV_TYPE=LOCAL for local. Then run: python spider_trading_bot.py")
else:
    print("\n[OK] Config looks correct for local (ENV_TYPE=LOCAL, BOT_TOKEN_DEV set and different from LIVE).")
    print("     If local still does not respond, check: (1) Local process is actually running, (2) No firewall blocking Telegram.")
print()
