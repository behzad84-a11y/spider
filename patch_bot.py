import sys
import os

file_path = 'spider_trading_bot.py'
if not os.path.exists(file_path):
    print(f"File {file_path} not found.")
    sys.exit(1)

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

with open(file_path, 'w', encoding='utf-8', newline='\r\n') as f:
    for line in lines:
        if 'input("Press Enter to exit...")' in line:
            f.write(line.replace('input("Press Enter to exit...")', '# Removed input for background mode'))
        else:
            f.write(line)

print("Patch applied successfully.")
