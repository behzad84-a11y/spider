#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPS Deployment Diagnostic Tool
Checks if bot is properly configured for LOCAL or VPS mode
"""

import os
import sys
from pathlib import Path
from datetime import datetime

class DeploymentDiagnostics:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.info = []
        self.success = []
        
    def check_env_file(self):
        """Check if .env file exists and has correct values"""
        print("\n[1] Checking .env file...")
        
        if not Path(".env").exists():
            self.errors.append("❌ .env file not found!")
            return False
            
        self.success.append("✓ .env file exists")
        
        # Read .env
        env_vars = {}
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
        
        # Check critical variables
        critical_vars = ["ENV_TYPE", "MODE", "BOT_TOKEN", "EXCHANGE_TYPE"]
        for var in critical_vars:
            if var in env_vars:
                self.success.append(f"✓ {var} = {env_vars[var]}")
            else:
                self.warnings.append(f"⚠ {var} not found in .env")
        
        # Check environment mode
        env_type = env_vars.get("ENV_TYPE", "UNKNOWN").upper()
        mode = env_vars.get("MODE", "UNKNOWN").upper()
        
        if env_type == "VPS":
            if mode != "LIVE":
                self.warnings.append(f"⚠ ENV_TYPE=VPS but MODE={mode} (should be LIVE)")
        elif env_type == "LOCAL":
            if mode != "DEV":
                self.warnings.append(f"⚠ ENV_TYPE=LOCAL but MODE={mode} (should be DEV)")
        
        return True
    
    def check_vps_env(self):
        """Check if vps.env exists"""
        print("\n[2] Checking vps.env file...")
        
        if Path("vps.env").exists():
            self.success.append("✓ vps.env found (for VPS deployment)")
            
            # Check contents
            with open("vps.env", "r", encoding="utf-8") as f:
                content = f.read()
                if "ENV_TYPE=VPS" in content and "MODE=LIVE" in content:
                    self.success.append("✓ vps.env is properly configured")
                else:
                    self.warnings.append("⚠ vps.env may not have correct VPS settings")
        else:
            self.warnings.append("⚠ vps.env not found (needed for proper VPS deployment)")
    
    def check_bot_file(self):
        """Check if main bot file exists and is valid"""
        print("\n[3] Checking bot file...")
        
        if not Path("spider_trading_bot.py").exists():
            self.errors.append("❌ spider_trading_bot.py not found!")
            return False
        
        self.success.append("✓ spider_trading_bot.py exists")
        
        # Check syntax
        try:
            import py_compile
            py_compile.compile("spider_trading_bot.py", doraise=True)
            self.success.append("✓ Python syntax is valid")
        except py_compile.PyCompileError as e:
            self.errors.append(f"❌ Python syntax error: {e}")
            return False
        
        return True
    
    def check_config(self):
        """Check config.py"""
        print("\n[4] Checking config.py...")
        
        if not Path("config.py").exists():
            self.errors.append("❌ config.py not found!")
            return False
        
        self.success.append("✓ config.py exists")
        
        # Check if it reads ENV_TYPE
        with open("config.py", "r", encoding="utf-8") as f:
            content = f.read()
            if "ENV_TYPE" in content:
                self.success.append("✓ config.py reads ENV_TYPE")
            else:
                self.warnings.append("⚠ config.py may not properly read ENV_TYPE")
        
        return True
    
    def check_requirements(self):
        """Check if requirements.txt exists"""
        print("\n[5] Checking requirements.txt...")
        
        if Path("requirements.txt").exists():
            self.success.append("✓ requirements.txt exists")
            
            # Check size (should have some content)
            size = Path("requirements.txt").stat().st_size
            if size > 50:
                self.success.append(f"✓ requirements.txt has content ({size} bytes)")
            else:
                self.warnings.append("⚠ requirements.txt seems very small")
        else:
            self.warnings.append("⚠ requirements.txt not found")
    
    def check_scripts(self):
        """Check deployment scripts"""
        print("\n[6] Checking deployment scripts...")
        
        scripts = [
            "SwitchToVPS.bat",
            "HardenedDeploy.ps1",
            "DeployToVPS_Fixed.ps1",
            "QuickSwitch.bat"
        ]
        
        found_scripts = []
        for script in scripts:
            if Path(script).exists():
                found_scripts.append(script)
                self.success.append(f"✓ {script} found")
            else:
                self.warnings.append(f"⚠ {script} not found")
        
        if found_scripts:
            self.success.append(f"✓ Found {len(found_scripts)} deployment scripts")
        else:
            self.errors.append("❌ No deployment scripts found!")
    
    def check_database(self):
        """Check trades.db"""
        print("\n[7] Checking database...")
        
        if Path("trades.db").exists():
            size = Path("trades.db").stat().st_size
            self.success.append(f"✓ trades.db exists ({size} bytes)")
        else:
            self.info.append("ℹ trades.db not found (will be created on first run)")
    
    def check_ssh_tools(self):
        """Check if SSH tools are available"""
        print("\n[8] Checking SSH tools (for VPS deployment)...")
        
        tools = [
            ("plink.exe", "PuTTY link (SSH client)"),
            ("pscp.exe", "PuTTY SCP (file transfer)"),
            ("WinSCP.com", "WinSCP (SFTP client)")
        ]
        
        for tool, description in tools:
            if Path(tool).exists():
                self.success.append(f"✓ {tool} ({description}) found")
            else:
                self.warnings.append(f"⚠ {tool} ({description}) not found")
    
    def get_current_env(self):
        """Get current environment"""
        print("\n" + "="*50)
        print("CURRENT ENVIRONMENT STATUS")
        print("="*50)
        
        if Path(".env").exists():
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and "=" in line and not line.startswith("#"):
                        key, value = line.split("=", 1)
                        if key.strip() in ["ENV_TYPE", "MODE", "EXCHANGE_TYPE"]:
                            print(f"  {key.strip()}: {value.strip()}")
    
    def print_report(self):
        """Print diagnostic report"""
        print("\n" + "="*50)
        print("DIAGNOSTIC REPORT")
        print("="*50)
        
        if self.success:
            print(f"\n✓ SUCCESSES ({len(self.success)}):")
            for msg in self.success:
                print(f"  {msg}")
        
        if self.warnings:
            print(f"\n⚠ WARNINGS ({len(self.warnings)}):")
            for msg in self.warnings:
                print(f"  {msg}")
        
        if self.errors:
            print(f"\n❌ ERRORS ({len(self.errors)}):")
            for msg in self.errors:
                print(f"  {msg}")
        
        if self.info:
            print(f"\nℹ INFO ({len(self.info)}):")
            for msg in self.info:
                print(f"  {msg}")
        
        # Summary
        print("\n" + "="*50)
        if not self.errors:
            print("✓ STATUS: Ready for deployment!")
            status_code = 0
        else:
            print("❌ STATUS: Issues detected - fix errors before deploying")
            status_code = 1
        print("="*50)
        
        return status_code
    
    def print_recommendations(self):
        """Print recommendations based on current state"""
        print("\n" + "="*50)
        print("RECOMMENDATIONS")
        print("="*50)
        
        # Read current env type
        env_type = "UNKNOWN"
        if Path(".env").exists():
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("ENV_TYPE="):
                        env_type = line.split("=")[1].strip().upper()
                        break
        
        if env_type == "LOCAL":
            print("\n📍 You are in LOCAL mode:")
            print("  • Bot will use LOCAL .env for testing")
            print("  • Paper trading only (no real trades)")
            print("  • Use QuickSwitch.bat to deploy to VPS when ready")
            print("  • Run: python spider_trading_bot.py")
        elif env_type == "VPS":
            print("\n🌐 You are in VPS mode:")
            print("  • Bot is configured for remote VPS")
            print("  • Make sure VPS credentials are correct")
            print("  • Run DeployToVPS_Fixed.ps1 to deploy")
            print("  • Check VPS status with plink commands")
        else:
            print("\n❓ Environment unknown - check .env file")
        
        if not Path("vps.env").exists():
            print("\n⚠ vps.env not found:")
            print("  • Create vps.env for proper VPS deployment")
            print("  • Run QuickSwitch.bat and select 'Deploy and Switch to VPS'")
        
        print("\n📚 Quick Commands:")
        print("  • Switch to LOCAL: QuickSwitch.bat → Option 2")
        print("  • Deploy to VPS:   QuickSwitch.bat → Option 3")
        print("  • View .env:       QuickSwitch.bat → Option 4")
        print("  • Run bot locally: python spider_trading_bot.py")
    
    def run_full_check(self):
        """Run all checks"""
        print("\n" + "="*50)
        print("VPS DEPLOYMENT DIAGNOSTIC TOOL")
        print("="*50)
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Working Directory: {os.getcwd()}")
        
        self.check_env_file()
        self.check_vps_env()
        self.check_bot_file()
        self.check_config()
        self.check_requirements()
        self.check_scripts()
        self.check_database()
        self.check_ssh_tools()
        
        self.get_current_env()
        status = self.print_report()
        self.print_recommendations()
        
        print("\n" + "="*50)
        
        return status

if __name__ == "__main__":
    diag = DeploymentDiagnostics()
    exit_code = diag.run_full_check()
    
    input("\nPress Enter to exit...")
    sys.exit(exit_code)
