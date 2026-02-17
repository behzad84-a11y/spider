import os
import sys

def prepare_env():
    input_file = '.env'
    output_file = 'vps_generated.env'
    
    print(f"--- PREPARING VPS ENVIRONMENT (STRICT MODE) ---") 
    
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found.")
        sys.exit(1)

    # Read all lines
    config = {}
    with open(input_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            config[k.strip()] = v.strip()

    # STRICT: We build the new file from scratch.
    new_lines = []
    
    # 1. ENV_TYPE
    new_lines.append(f"ENV_TYPE=VPS")
    
    # 2. TOKEN LOGIC
    # Must use BOT_TOKEN_LIVE. 
    token_live = config.get('BOT_TOKEN_LIVE')
    if not token_live:
        print("CRITICAL ERROR: BOT_TOKEN_LIVE is missing from your local .env file!")
        print("Action: Please add BOT_TOKEN_LIVE=<your_vps_token> to local .env and retry.")
        sys.exit(1)
            
    new_lines.append(f"BOT_TOKEN_LIVE={token_live}")
    
    # 3. MODE LOGIC
    # "prefer DEFAULT_VPS_MODE if present; else keep MODE if it is LIVE or PAPER; else set MODE=LIVE"
    raw_mode = config.get('MODE', 'DEV').upper()
    default_vps = config.get('DEFAULT_VPS_MODE', '').upper()
    
    final_mode = 'LIVE'
    if default_vps:
        final_mode = default_vps
    elif raw_mode in ['LIVE', 'PAPER']:
        final_mode = raw_mode
    
    new_lines.append(f"MODE={final_mode}")
    
    # 4. OTHER KEYS
    # "Copy ONLY non-secret operational keys"
    # To be safe, we block known bad keys and allow others, or whitelist?
    # Whitelist is safer but harder to maintain. Blacklist is easier.
    # Block keys: ENV_TYPE, BOT_TOKEN, BOT_TOKEN_DEV, BOT_TOKEN_LIVE, MODE
    blocked_keys = ['ENV_TYPE', 'BOT_TOKEN', 'BOT_TOKEN_DEV', 'BOT_TOKEN_LIVE', 'MODE']
    
    count = 0
    for k, v in config.items():
        if k in blocked_keys:
            continue
        new_lines.append(f"{k}={v}")
        count += 1

    # 5. WRITE
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(new_lines))
        f.write('\n')
        
    print(f"Success: Generated {output_file}")
    print(f" - ENV_TYPE=VPS enforced")
    print(f" - BOT_TOKEN_LIVE included")
    print(f" - MODE={final_mode} enforced")
    print(f" - Copied {count} other config keys")
    print(f" - BLOCKED: BOT_TOKEN, BOT_TOKEN_DEV")
    print(f"---------------------------------")

if __name__ == "__main__":
    prepare_env()
