import subprocess
import sys
import time
import logging
from datetime import datetime

# Configure logging
# Force UTF-8 for stdout checks
sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("runner.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

BOT_SCRIPT = "spider_trading_bot.py"
MAX_RESTARTS_PER_HOUR = 10
RESTART_WINDOW = 3600

kv_restarts = []

def clean_old_restarts():
    now = time.time()
    global kv_restarts
    kv_restarts = [t for t in kv_restarts if now - t < RESTART_WINDOW]

def run_bot():
    while True:
        clean_old_restarts()
        if len(kv_restarts) >= MAX_RESTARTS_PER_HOUR:
            logging.critical(f"Too many restarts ({len(kv_restarts)}) in the last hour. Runner giving up.")
            sys.exit(1)

        logging.info("[RUNNER] Starting Spider Bot...")
        start_time = time.time()
        
        # Run the bot
        try:
            # We use same python interpreter
            process = subprocess.Popen([sys.executable, BOT_SCRIPT])
            exit_code = process.wait()
        except Exception as e:
            logging.error(f"Failed to launch bot: {e}")
            time.sleep(5)
            continue

        runtime = time.time() - start_time
        logging.warning(f"[RUNNER] Bot exited with code {exit_code}. Runtime: {runtime:.2f}s")
        
        # If user stopped it manually (0) or via signal, maybe we shouldn't restart?
        # But for VPS, we usually want it always up.
        # Exit code 0 usually means clean shutdown.
        if exit_code == 0:
            logging.info("Bot stopped cleanly. Restarting in 5s...")
        else:
            # Check for conflict (exit code 1 is generic, but usually crashes are >0)
            logging.warning("Bot crash detected. Restarting in 10s...")
            # If runtime was very short, it might be a loop (e.g. 409 conflict persisting)
            if runtime < 10:
                logging.warning("Fast crash detected! Backing off for 30s to clear Telegram conflict...")
                time.sleep(30)
            else:
                time.sleep(10)

        kv_restarts.append(time.time())

if __name__ == "__main__":
    try:
        run_bot()
    except KeyboardInterrupt:
        logging.info("Runner stopped by user.")
