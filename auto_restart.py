import time
import os
import subprocess
import sys
from datetime import datetime

WATCH_FILE = "spider_trading_bot.py"
BOT_PROCESS = None

def start_bot():
    global BOT_PROCESS
    print(f"[{datetime.now()}] Starting bot...")
    # Using 'start' to open in new window (optional) or just subprocess
    # For VPS comfort, running directly in this console is better so we see logs here
    BOT_PROCESS = subprocess.Popen([sys.executable, WATCH_FILE])

def stop_bot():
    global BOT_PROCESS
    if BOT_PROCESS:
        print(f"[{datetime.now()}] Stopping bot...")
        BOT_PROCESS.terminate()
        try:
            BOT_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            BOT_PROCESS.kill()
        BOT_PROCESS = None

def get_file_mtime():
    try:
        return os.path.getmtime(WATCH_FILE)
    except OSError:
        return 0

def main():
    if not os.path.exists(WATCH_FILE):
        print(f"Error: {WATCH_FILE} not found!")
        return

    last_mtime = get_file_mtime()
    start_bot()

    print(f"[{datetime.now()}] Watching {WATCH_FILE} for changes...")

    try:
        while True:
            time.sleep(2)
            current_mtime = get_file_mtime()
            
            if current_mtime != last_mtime:
                print(f"[{datetime.now()}] Change detected in {WATCH_FILE}! Restarting...")
                stop_bot()
                time.sleep(1)
                start_bot()
                last_mtime = current_mtime
            
            # Also watch strategies
            for strat_file in ["gln_strategy.py", "gln_forex_strategy.py"]:
                if os.path.exists(strat_file):
                    m = os.path.getmtime(strat_file)
                    if m > last_mtime:
                        print(f"[{datetime.now()}] Change detected in {strat_file}! Restarting...")
                        stop_bot()
                        time.sleep(1)
                        start_bot()
                        last_mtime = m
                
    except KeyboardInterrupt:
        stop_bot()
        print("Watcher stopped.")

if __name__ == "__main__":
    main()
