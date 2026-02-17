import os
import socket
import getpass
import platform
from datetime import datetime, timedelta

# Mocking the environment detection logic from spider_trading_bot.py
def detect_env():
    hostname = socket.gethostname().upper()
    username = getpass.getuser().lower()
    
    if any(term in hostname for term in ["VPS", "IONOS", "STRATO", "WIN-", "SERVER"]):
        run_env = "VPS"
    elif username in ["administrator", "root"]:
        run_env = "VPS"
    elif "behza" in username or "desktop" in hostname.lower():
        run_env = "LOCAL"
    else:
        run_env = "LOCAL"
    return run_env, hostname, username

def get_where_msg(run_env, hostname, username, start_time):
    uptime = datetime.now() - start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    uptime_str = f"{days} روز، {hours} ساعت و {minutes} دقیقه" if days > 0 else f"{hours} ساعت و {minutes} دقیقه"
    
    env_labels = {
        "VPS": "🚀 سرور مجازی (Remote VPS)",
        "LOCAL": "💻 لپ‌تاپ شخصی (Local/Gravity)",
        "IDE/CI": "🛠 محیط توسعه (IDE)",
        "UNKNOWN": "❓ نامشخص"
    }
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cwd = os.getcwd()
    pid = os.getpid()
    
    msg = (
        f"📍 اطلاعات اجرای ربات\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏠 محیط: {env_labels.get(run_env, run_env)}\n"
        f"⏱ زمان فعالیت: {uptime_str}\n"
        f"🖥 هاست: {hostname}\n"
        f"👤 کاربر: {user}\n"
        f"🔢 شناسه (PID): {pid}\n"
        f"📂 مسیر: {cwd}\n"
        f"🕒 زمان سرور: {now_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    return msg

if __name__ == "__main__":
    env, host, user = detect_env()
    start_time = datetime.now() - timedelta(hours=2, minutes=15)
    output = get_where_msg(env, host, user, start_time)
    
    with open("where_output_verification.txt", "w", encoding="utf-8") as f:
        f.write("DEMONSTRATING /where OUTPUT LOCALLY:\n")
        f.write(output)
    
    print("Verification output saved to where_output_verification.txt")
