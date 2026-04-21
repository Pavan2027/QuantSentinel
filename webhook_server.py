"""
webhook_server.py
------------------
Receives Upstox token via webhook after user approves on phone.
Run this alongside the bot on EC2 or locally with ngrok.

Usage:
    python webhook_server.py

Endpoints:
    POST /webhook/token    — Upstox sends token here after user approval
    GET  /health           — Health check
    GET  /token-status     — Check if today's token is valid
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, ".")

from flask import Flask, request, jsonify
from utils.db import init_db
from utils.logger import get_logger

log = get_logger("webhook_server")
app = Flask(__name__)
init_db()


@app.route("/webhook/token", methods=["POST"])
def token_webhook():
    """Receive token from Upstox after user approves on WhatsApp/app."""
    data = request.get_json(force=True, silent=True)

    if not data:
        log.warning("Webhook received empty payload")
        return jsonify({"status": "error", "message": "Empty payload"}), 400

    log.info(f"Webhook received: message_type={data.get('message_type')}")

    if data.get("message_type") == "access_token":
        token = data.get("access_token")
        if not token:
            return jsonify({"status": "error", "message": "No token"}), 400

        from execution.upstox_auth import save_token_from_webhook
        save_token_from_webhook(token)

        log.info(f"✅ Token saved via webhook | "
                 f"user={data.get('user_id', '?')} | "
                 f"expires={data.get('expires_at', '?')}")

        return jsonify({"status": "ok", "message": "Token saved"})

    return jsonify({"status": "ignored"}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "QuantSentinel Webhook Server",
    })


@app.route("/token-status", methods=["GET"])
def token_status():
    from execution.upstox_auth import is_token_valid, get_token_from_db
    valid = is_token_valid()
    token = get_token_from_db()
    return jsonify({
        "token_valid": valid,
        "token_preview": f"{token[:10]}..." if token else None,
    })


if __name__ == "__main__":
    port = int(os.getenv("WEBHOOK_PORT", 5000))
    log.info(f"Webhook server starting on port {port}")
    log.info(f"Token endpoint: POST http://localhost:{port}/webhook/token")
    app.run(host="0.0.0.0", port=port, debug=False)