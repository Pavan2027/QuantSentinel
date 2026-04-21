"""
execution/upstox_auth.py
-------------------------
Upstox V3 token request flow.

How it works:
  1. Bot POSTs to Upstox with client_id + client_secret
  2. Upstox sends notification to user via WhatsApp + Upstox app
  3. User approves on their phone
  4. Upstox POSTs the token to your webhook URL
  5. Bot reads token from webhook or polls DB

Requirements:
  - A publicly accessible webhook URL (use ngrok locally, EC2 URL on server)
  - Set UPSTOX_WEBHOOK_URL in .env (e.g. https://yourserver.com/webhook/token)
"""

import os
import time
import threading
from datetime import date, datetime, timezone

import requests

from utils.db import get_conn, init_db
from utils.logger import get_logger

log = get_logger("upstox_auth")

TOKEN_URL_V3 = "https://api.upstox.com/v3/login/auth/token/request/{client_id}"
TOKEN_KEY      = "UPSTOX_ACCESS_TOKEN"
TOKEN_DATE_KEY = "UPSTOX_TOKEN_DATE"


def _get_credentials() -> tuple[str, str]:
    api_key    = os.getenv("UPSTOX_API_KEY", "")
    api_secret = os.getenv("UPSTOX_API_SECRET", "")
    if not api_key or not api_secret:
        raise EnvironmentError(
            "UPSTOX_API_KEY and UPSTOX_API_SECRET must be set in .env"
        )
    return api_key, api_secret


def _save_token(token: str):
    init_db()
    today = date.today().isoformat()
    with get_conn() as conn:
        for key, val in [(TOKEN_KEY, token), (TOKEN_DATE_KEY, today)]:
            conn.execute("""
                INSERT OR REPLACE INTO control_flags (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, val, datetime.now(timezone.utc).isoformat()))
    log.info("Upstox access token saved")


def _load_token() -> tuple[str | None, str | None]:
    init_db()
    with get_conn() as conn:
        t = conn.execute(
            "SELECT value FROM control_flags WHERE key = ?", (TOKEN_KEY,)
        ).fetchone()
        d = conn.execute(
            "SELECT value FROM control_flags WHERE key = ?", (TOKEN_DATE_KEY,)
        ).fetchone()
    return (t["value"] if t else None), (d["value"] if d else None)


def is_token_valid() -> bool:
    token, token_date = _load_token()
    if not token or not token_date:
        return False
    return token_date == date.today().isoformat()


def get_token_from_db() -> str | None:
    if is_token_valid():
        token, _ = _load_token()
        return token
    return None


def request_token_v3() -> str:
    """
    Send token request to Upstox V3 API.
    User gets notified on WhatsApp + Upstox app to approve.
    Token is delivered to your webhook URL.

    Returns the token once received (polls DB for up to 10 minutes).
    """
    api_key, api_secret = _get_credentials()

    url = TOKEN_URL_V3.format(client_id=api_key)
    response = requests.post(
        url,
        headers={"accept": "application/json", "Content-Type": "application/json"},
        json={"client_secret": api_secret},
        timeout=15,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Token request failed: {response.status_code} — {response.text}"
        )

    data = response.json()
    expiry_ms = int(data.get("data", {}).get("authorization_expiry", 0))
    expiry_dt = datetime.fromtimestamp(expiry_ms / 1000).strftime("%H:%M %d-%b")

    print("\n" + "="*60)
    print("TOKEN REQUEST SENT")
    print("="*60)
    print("✅ Upstox has notified you via WhatsApp and the Upstox app.")
    print("   Please approve the request on your phone.")
    print(f"   Request expires at: {expiry_dt}")
    print("\nWaiting for approval (polls every 10s, timeout 10 min)...")

    # Poll DB for up to 10 minutes
    # Your webhook endpoint must call save_token_from_webhook() when it receives the token
    for i in range(60):
        time.sleep(10)
        if is_token_valid():
            token, _ = _load_token()
            print(f"\n✅ Token received and saved! (after {(i+1)*10}s)")
            return token
        print(f"  Waiting... ({(i+1)*10}s elapsed)", end="\r")

    raise TimeoutError(
        "Token not received after 10 minutes. "
        "Check if your webhook URL is correctly configured in Upstox developer portal."
    )


def save_token_from_webhook(token: str):
    """
    Call this from your webhook endpoint when Upstox POSTs the token.
    Your webhook handler in Flask/FastAPI should call this function.

    Example Flask webhook:
        @app.route("/webhook/token", methods=["POST"])
        def token_webhook():
            data = request.json
            if data.get("message_type") == "access_token":
                save_token_from_webhook(data["access_token"])
            return {"status": "ok"}
    """
    _save_token(token)
    log.info("Token received via webhook and saved")


def get_valid_token() -> str:
    """Return valid token, requesting a new one if expired."""
    if is_token_valid():
        token, _ = _load_token()
        log.info("Using existing valid Upstox token")
        return token
    log.info("Token expired or missing — requesting new token via V3 API")
    return request_token_v3()


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    token = request_token_v3()
    print(f"Token: {token[:20]}...{token[-10:]}")