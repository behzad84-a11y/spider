# 🚀 Quick Start - VPS Deployment Fix

## مسئلہ خلاصہ (Problem Summary)

✗ Deploy says "OK" but bot doesn't work on VPS
✓ Your bot has `ENV_TYPE=LOCAL` so it stays in LOCAL mode even on VPS

---

## ✅ 3-Step Fix

### Step 1: Copy New Files to Your Project

Copy these files from the package into your bot directory:

```
✓ vps.env                  (new - VPS environment configuration)
✓ DeployToVPS_Fixed.ps1    (new - improved deployment script)
✓ QuickSwitch.bat          (new - easy switcher)
✓ diagnose_deployment.py   (new - diagnostic tool)
```

### Step 2: Run Diagnostic Check

```bash
python diagnose_deployment.py
```

This will show you current status and any issues.

### Step 3: Deploy to VPS

**Option A - Using New Easy Switcher (Recommended):**
```batch
QuickSwitch.bat
# Select option 3: Deploy and Switch to VPS
```

**Option B - Using PowerShell Directly:**
```powershell
powershell -ExecutionPolicy Bypass -File DeployToVPS_Fixed.ps1
```

---

## What Changed & Why It Works

| Issue | Solution |
|-------|----------|
| Bot says "ok" but doesn't run | Now properly sets `ENV_TYPE=VPS` on server |
| Can't switch environments | QuickSwitch.bat handles env switching |
| No way to verify setup | diagnose_deployment.py checks everything |
| Old deploy script unclear | DeployToVPS_Fixed.ps1 explains each step |

---

## File-by-File Explanation

### 📄 vps.env
Contains settings **specifically for VPS**:
```
ENV_TYPE=VPS           ← This is the KEY difference
MODE=LIVE              ← Real trading on VPS
EXCHANGE_TYPE=coinex   ← Same as local
BOT_TOKEN=...          ← Same token
```

**When deployed:**
- This file is copied to VPS as `.env`
- Bot reads it and knows it's running on VPS
- Bot enables LIVE trading

---

### 📜 DeployToVPS_Fixed.ps1
**NEW improved PowerShell deployment script** with:
- ✓ Automatic vps.env detection
- ✓ Stops local bot properly
- ✓ Uploads vps.env as .env to VPS
- ✓ Verifies environment was switched correctly
- ✓ Clear step-by-step output
- ✓ Tells you what went wrong if something fails

**Run once with:**
```powershell
powershell -ExecutionPolicy Bypass -File DeployToVPS_Fixed.ps1
```

---

### 🎮 QuickSwitch.bat
**Easy menu-based switcher** with options:
1. Switch to VPS mode (local .env → vps.env)
2. Switch to LOCAL mode (vps.env → .env)
3. Deploy and Switch to VPS (all-in-one)
4. View current .env
5. Exit

**Run with:**
```batch
QuickSwitch.bat
```

**Use this for daily switching!**

---

### 🔍 diagnose_deployment.py
**Diagnostic tool that checks:**
- ✓ .env file exists and has right values
- ✓ vps.env exists (needed for VPS)
- ✓ spider_trading_bot.py has no syntax errors
- ✓ config.py properly reads ENV_TYPE
- ✓ requirements.txt exists
- ✓ Deployment scripts are present
- ✓ SSH tools (plink, pscp) are available
- ✓ Database files exist

**Run with:**
```bash
python diagnose_deployment.py
```

**Shows current environment and issues!**

---

## Step-by-Step Workflow

### First Time Setup

```
1. Copy 4 files to your project ↓
2. Run: python diagnose_deployment.py ↓
3. Fix any errors shown ↓
4. Run: QuickSwitch.bat → Option 3 ↓
5. Wait 30 seconds ↓
6. Check bot is running on VPS ✓
```

### Daily Usage

**To test locally:**
```batch
QuickSwitch.bat → Option 2 (Switch to LOCAL)
python spider_trading_bot.py
```

**To run on VPS:**
```batch
QuickSwitch.bat → Option 3 (Deploy and Switch to VPS)
```

**To check current mode:**
```batch
QuickSwitch.bat → Option 4 (View .env)
```

---

## Troubleshooting

### Problem: Bot still doesn't work on VPS

**Check 1: Verify environment was switched**
```powershell
plink -pw "000cdewsxzaQ" Administrator@87.106.210.120 "type C:\Users\Administrator\ok\.env | find ENV_TYPE"
```
Should show: `ENV_TYPE=VPS`

**Check 2: See bot errors**
```powershell
plink -pw "000cdewsxzaQ" Administrator@87.106.210.120 "type C:\Users\Administrator\ok\bot.log"
```

**Check 3: Check if Python is running**
```powershell
plink -pw "000cdewsxzaQ" Administrator@87.106.210.120 "tasklist | find python"
```

### Problem: SSH tools not found

Download from PuTTY:
- plink.exe: https://www.putty.org/
- pscp.exe: https://www.putty.org/

Place in your project folder.

### Problem: VPS credentials wrong

Check HardenedDeploy.ps1 or DeployToVPS_Fixed.ps1:
```powershell
$VPS_IP = "87.106.210.120"      ← Your VPS IP
$VPS_USER = "Administrator"      ← Your VPS username
$VPS_PASS = "000cdewsxzaQ"       ← Your VPS password
```

---

## Key Concepts

**LOCAL Mode** (for development):
- .env contains `ENV_TYPE=LOCAL`
- Uses local database
- Paper trading only
- You run `python spider_trading_bot.py`

**VPS Mode** (for production):
- vps.env contains `ENV_TYPE=VPS`
- Gets copied to VPS as .env
- Live trading enabled
- Bot auto-starts via scheduled task

---

## Files to Replace/Update

| File | Action | Why |
|------|--------|-----|
| vps.env | CREATE (new) | Separate config for VPS |
| DeployToVPS_Fixed.ps1 | CREATE (new) | Better deployment script |
| QuickSwitch.bat | CREATE (new) | Easy environment switcher |
| diagnose_deployment.py | CREATE (new) | Verify setup |
| HardenedDeploy.ps1 | KEEP (optional) | Can still use old version |

---

## Common Commands

```bash
# Diagnose everything
python diagnose_deployment.py

# Switch environments easily
QuickSwitch.bat

# Deploy to VPS (using new script)
powershell -ExecutionPolicy Bypass -File DeployToVPS_Fixed.ps1

# Deploy to VPS (using old script - still works)
SwitchToVPS.bat

# Run bot locally
python spider_trading_bot.py

# Check VPS status
plink -pw "000cdewsxzaQ" Administrator@87.106.210.120 "tasklist | find python"

# View VPS logs
plink -pw "000cdewsxzaQ" Administrator@87.106.210.120 "type C:\Users\Administrator\ok\bot.log"
```

---

## FAQ

**Q: Should I keep .env or use vps.env?**
A: Keep both!
- .env = for LOCAL testing
- vps.env = for VPS deployment

**Q: What if I want to change VPS credentials?**
A: Edit vps.env, then redeploy

**Q: Can I run LOCAL and VPS at same time?**
A: No! Stop local bot before deploying to VPS

**Q: How long does deployment take?**
A: Usually 20-30 seconds

**Q: How do I know deployment worked?**
A: Run diagnose_deployment.py and check VPS logs

---

## Success Indicators ✓

When working correctly, you should see:

1. **Local test:**
   ```
   python spider_trading_bot.py
   → Bot starts
   → Shows "Environment: LOCAL"
   → Paper trading active
   ```

2. **VPS deployment:**
   ```
   QuickSwitch.bat → Option 3
   → Shows "DEPLOYMENT SUCCESSFUL"
   → Files uploaded
   → Bot auto-starting on VPS
   ```

3. **VPS running:**
   ```
   plink -pw "password" Administrator@IP "tasklist | find python"
   → Shows python.exe process
   ```

---

## Support

If something still doesn't work:

1. Run `diagnose_deployment.py` - shows all issues
2. Check VPS logs with plink command above
3. Verify SSH tools are present
4. Verify VPS IP and credentials
5. Try old script: `SwitchToVPS.bat` (fallback)

---

**Last Updated:** Feb 15, 2026
**Version:** 4.0 (with ENV_TYPE switching fix)
