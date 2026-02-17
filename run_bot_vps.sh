#!/bin/bash

# Kill existing bot process
pkill -f spider_trading_bot.py

# Wait a moment
sleep 2

# Install dependencies if needed (optional)
# pip install -r requirements.txt

# Run bot in background with nohup (keeps running after logout)
nohup python3 spider_trading_bot.py > bot.log 2>&1 &

echo "Bot restarted on VPS! Logs are in bot.log"
