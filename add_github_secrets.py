#!/usr/bin/env python3
"""
GitHub Secrets Configuration Script

This script adds repository secrets needed for R2 deployment.
Requires: GitHub CLI (gh) to be installed and authenticated.

Usage:
    python add_github_secrets.py
"""

import os
import subprocess
import sys
from pathlib import Path

def run_command(cmd):
    """Run shell command and return output."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            shell=True
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)

def get_secrets_from_env():
    """Load secrets from .env file."""
    env_file = Path(".env")
    if not env_file.exists():
        print("❌ .env file not found!")
        return None
    
    secrets = {}
    required_keys = [
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID", 
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET"
    ]
    
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                if key in required_keys:
                    secrets[key] = value
    
    # Verify all required secrets are present
    missing = [k for k in required_keys if k not in secrets]
    if missing:
        print(f"❌ Missing secrets in .env: {', '.join(missing)}")
        return None
    
    return secrets

def check_gh_auth():
    """Check if gh is authenticated."""
    code, _, _ = run_command("& 'C:\\Program Files\\GitHub CLI\\gh.exe' auth status")
    return code == 0

def add_secret(key, value):
    """Add a secret to GitHub."""
    # Escape special characters in value
    if '"' in value or '`' in value:
        value = value.replace('"', '`"')
    
    cmd = f'''& 'C:\\Program Files\\GitHub CLI\\gh.exe' secret set {key} --body "{value}"'''
    code, stdout, stderr = run_command(cmd)
    
    if code == 0:
        print(f"✅ {key} added successfully")
        return True
    else:
        print(f"❌ Failed to add {key}")
        if stderr:
            print(f"   Error: {stderr}")
        return False

def main():
    """Main function."""
    print("=" * 60)
    print("GitHub Secrets Configuration")
    print("=" * 60)
    print()
    
    # Check if running from correct directory
    if not Path(".env").exists():
        print("❌ .env file not found. Run from repository root directory.")
        sys.exit(1)
    
    # Check gh authentication
    print("Checking GitHub CLI authentication...")
    endpoint = "github.com"
    check_code, auth_out, _ = run_command(f"& 'C:\\Program Files\\GitHub CLI\\gh.exe' auth status -h {endpoint}")
    
    if check_code != 0:
        print()
        print("⚠️  GitHub CLI is not authenticated.")
        print()
        print("To authenticate, run:")
        print("  & 'C:\\Program Files\\GitHub CLI\\gh.exe' auth login")
        print()
        print("Then run this script again.")
        sys.exit(1)
    
    print("✅ GitHub CLI is authenticated")
    print()
    
    # Load secrets from .env
    print("Loading secrets from .env...")
    secrets = get_secrets_from_env()
    
    if not secrets:
        sys.exit(1)
    
    print(f"✅ Found {len(secrets)} secrets")
    print()
    
    # Add secrets to GitHub
    print("Adding secrets to GitHub repository...")
    print()
    
    failed = []
    for key, value in secrets.items():
        if not add_secret(key, value):
            failed.append(key)
    
    print()
    print("=" * 60)
    
    if failed:
        print(f"❌ Failed to add: {', '.join(failed)}")
        print()
        print("Manual steps:")
        print("1. Go to: https://github.com/AnasSfar/tsm-backend/settings/secrets/actions")
        print("2. Click 'New repository secret'")
        print("3. Add each secret:")
        for key, value in secrets.items():
            if key in failed:
                print(f"   - Name: {key}")
                print(f"     Value: {value}")
        sys.exit(1)
    else:
        print("✅ All secrets added successfully!")
        print()
        print("Next step: Push .github/workflows updates if any")
        sys.exit(0)

if __name__ == "__main__":
    main()
