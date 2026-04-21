#!/usr/bin/env python3
"""
deploy/refresh_token.py
------------------------
Run this each morning before 9:15 AM IST to refresh the Upstox token.
The bot cannot trade without a valid token.

Usage (local):
    python deploy/refresh_token.py

Usage (from laptop connecting to EC2):
    ssh ubuntu@YOUR_EC2_IP "cd QuantSentinel && source venv/bin/activate && python deploy/refresh_token.py"

What happens:
  1. Sends token request to Upstox V3 API
  2. Upstox sends WhatsApp + in-app notification to your phone
  3. You tap "Approve" on your phone
  4. Token is sent to your webhook URL automatically
  5. This script polls until token arrives (max 10 min)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from utils.db import init_db
init_db()

from execution.upstox_auth import is_token_valid, request_token_v3, get_token_from_db
from utils.logger import get_logger

log = get_logger("refresh_token")


def main():
    print("\n" + "="*50)
    print("  QuantSentinel — Daily Token Refresh")
    print("="*50)

    # Check if already valid
    if is_token_valid():
        token = get_token_from_db()
        print(f"\n✅ Token already valid for today.")
        print(f"   Token: {token[:15]}...{token[-8:]}")
        print(f"\nBot is ready to trade.\n")
        return

    print("\nRequesting new token from Upstox...")
    print("You will receive a WhatsApp and in-app notification.")
    print("Please approve it on your phone.\n")

    try:
        token = request_token_v3()
        print(f"\n✅ Token refreshed successfully!")
        print(f"   Token: {token[:15]}...{token[-8:]}")
        print(f"\nBot is ready to trade. Starting scheduler...\n")

        # Optionally restart the bot service to pick up new token
        restart = input("Restart the bot service now? [y/N]: ").strip().lower()
        if restart == "y":
            os.system("sudo systemctl restart quantsentinel")
            print("Bot service restarted.")

    except TimeoutError:
        print("\n❌ Token request timed out.")
        print("   Make sure your webhook URL is correctly set in the Upstox developer portal.")
        print("   Webhook URL should be: http://YOUR_EC2_IP:5000/webhook/token")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()