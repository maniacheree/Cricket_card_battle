# server.py
# Python Flask backend for the Telegram Web App.
# IMPORTANT: BOT_TOKEN must be set as a Railway environment variable.

import os
import hmac
import hashlib
import json
import time

from urllib.parse import parse_qsl
from flask import Flask, request, jsonify, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]


# =========================================================
# SERVE WEBSITE
# =========================================================

@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


# Serve website files:
# hero_gold.jpg
# battle_gold.jpg
# common_pack.png
# rare_pack.png
# epic_pack.png
# legendary_pack.png
# ultimate_pack.png
# payment_qr.jpg
# players.json
# etc.

@app.get("/<path:filename>")
def serve_file(filename):
    # Never interfere with API routes
    if filename.startswith("api/"):
        return jsonify({"error": "Not found"}), 404

    file_path = os.path.join(BASE_DIR, filename)

    # Security: don't allow paths outside the project folder
    if not os.path.abspath(file_path).startswith(BASE_DIR):
        return jsonify({"error": "Forbidden"}), 403

    if os.path.isfile(file_path):
        return send_from_directory(BASE_DIR, filename)

    return jsonify({"error": "Not found"}), 404


# =========================================================
# TELEGRAM INIT DATA VERIFICATION
# =========================================================

def verify_telegram_init_data(init_data: str):

    if not init_data:
        raise ValueError("Missing initData")

    data = dict(parse_qsl(init_data, keep_blank_values=True))

    received = data.pop("hash", None)

    if not received:
        raise ValueError("Missing hash")

    check = "\n".join(
        f"{k}={data[k]}"
        for k in sorted(data)
    )

    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated = hmac.new(
        secret,
        check.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received):
        raise ValueError("Invalid Telegram initData")

    # Optional freshness check
    auth_date = int(data.get("auth_date", "0"))

    if time.time() - auth_date > 86400:
        raise ValueError("Expired initData")

    return json.loads(data["user"])


# =========================================================
# TELEGRAM AUTH
# =========================================================

@app.post("/api/auth/telegram")
def auth():

    try:
        body = request.get_json(silent=True) or {}

        user = verify_telegram_init_data(
            body.get("initData")
        )

        # TODO:
        # Load/create this Telegram ID in PostgreSQL.

        return jsonify({
            "ok": True,
            "user": {
                "id": user["id"],
                "coins": 0,
                "cards": 0
            }
        })

    except Exception as e:

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 401


# =========================================================
# DEPOSITS
# =========================================================

@app.post("/api/deposits")
def deposits():

    try:
        body = request.get_json(silent=True) or {}

        user = verify_telegram_init_data(
            body.get("initData")
        )

        # TODO:
        # Save amount, coins, UTR and screenshot
        # to PostgreSQL/storage.
        #
        # DO NOT CREDIT COINS HERE.
        # Coins should be credited only after
        # admin approval.

        return jsonify({
            "ok": True,
            "user_id": user["id"],
            "status": "PENDING"
        })

    except Exception as e:

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 401


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
def health():

    return jsonify({
        "ok": True,
        "service": "Cricket Card Arena"
    })


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    port = int(os.getenv("PORT", "8000"))

    app.run(
        host="0.0.0.0",
        port=port
        )
