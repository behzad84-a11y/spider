import os

def load_env(path='.env'):
    """Simple manual .env loader."""
    # Try explicit path relative to this file first
    if path == '.env':
        script_dir = os.path.dirname(os.path.abspath(__file__))
        abs_path = os.path.join(script_dir, '.env')
        if os.path.exists(abs_path):
            path = abs_path
            
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            # STRICT: Overwrite existing environment variables to ensure .env is the source of truth
            os.environ[key] = value.strip().strip("'").strip('"')

# Load the .env file
load_env()

# Bot Configuration
# SECURITY: Separate tokens to prevent 409 Conflict
# STRICT MODE: No fallback to BOT_TOKEN - must use BOT_TOKEN_DEV (LOCAL) or BOT_TOKEN_LIVE (VPS)
BOT_TOKEN_DEV = os.getenv('BOT_TOKEN_DEV')
BOT_TOKEN_LIVE = os.getenv('BOT_TOKEN_LIVE')
# BOT_TOKEN is deprecated - kept only for legacy detection/warnings, never used as fallback
BOT_TOKEN = os.getenv('BOT_TOKEN')

ENV_TYPE = os.getenv('ENV_TYPE', 'LOCAL').upper()  # LOCAL or VPS
ALLOW_LOCAL_PAPER = os.getenv('ALLOW_LOCAL_PAPER', '0') == '1'

# Exchange Selection: 'coinex' or 'kucoin'
EXCHANGE_TYPE = os.getenv('EXCHANGE_TYPE', 'coinex')

# KuCoin Credentials
KUCOIN_API_KEY = os.getenv('KUCOIN_API_KEY')
KUCOIN_SECRET = os.getenv('KUCOIN_SECRET')
KUCOIN_PASSPHRASE = os.getenv('KUCOIN_PASSPHRASE')

# CoinEx Credentials
COINEX_API_KEY = os.getenv('COINEX_API_KEY')
COINEX_SECRET = os.getenv('COINEX_SECRET')

# Trading Mode Settings
MODE = os.getenv('MODE', 'DEV').upper()
DEFAULT_VPS_MODE = os.getenv('DEFAULT_VPS_MODE', 'PAPER').upper()

# Strategy Configuration
AUTO_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']

# AI Optimizer
AI_EVAL_INTERVAL = int(os.getenv('AI_EVAL_INTERVAL', '30'))    # minutes
AI_THRESHOLD = int(os.getenv('AI_THRESHOLD', '75'))             # 0–100

# Silent Manager (quiet hours, local time)
SILENT_START_HOUR = int(os.getenv('SILENT_START_HOUR', '23'))
SILENT_END_HOUR = int(os.getenv('SILENT_END_HOUR', '7'))
SILENT_ENABLED = os.getenv('SILENT_ENABLED', 'True').lower() in ('true', '1', 'yes')

# Digest Reporter
DIGEST_INTERVAL = int(os.getenv('DIGEST_INTERVAL', '60'))      # minutes
