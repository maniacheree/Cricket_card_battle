import os
import hmac
import hashlib
import json
import time

from urllib.parse import parse_qsl
from flask import Flask, request, jsonify, send_from_directory

# =========================================================
# APP SETUP
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")


# =========================================================
# WEBSITE
# =========================================================

@app.route("/", methods=["GET"])
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/<path:filename>", methods=["GET"])
def serve_file(filename):

    # Don't interfere with API routes
    if filename.startswith("api/"):
        return jsonify({"ok": False, "error": "Not found"}), 404

    file_path = os.path.abspath(os.path.join(BASE_DIR, filename))

    # Security check
    if not file_path.startswith(BASE_DIR):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    if os.path.isfile(file_path):
        return send_from_directory(BASE_DIR, filename)

    return jsonify({"ok": False, "error": "Not found"}), 404


# =========================================================
# TELEGRAM INIT DATA VERIFICATION
# =========================================================

def verify_telegram_init_data(init_data):

    if not init_data:
        raise ValueError("Missing initData")

    data = dict(
        parse_qsl(
            init_data,
            keep_blank_values=True
        )
    )

    received_hash = data.pop("hash", None)

    if not received_hash:
        raise ValueError("Missing hash")

    check_string = "\n".join(
        f"{key}={data[key]}"
        for key in sorted(data)
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(
        calculated_hash,
        received_hash
    ):
        raise ValueError("Invalid Telegram initData")

    # Check auth_date
    auth_date = int(
        data.get("auth_date", "0")
    )

    if auth_date <= 0:
        raise ValueError("Invalid auth_date")

    if time.time() - auth_date > 86400:
        raise ValueError("Expired initData")

    user_data = data.get("user")

    if not user_data:
        raise ValueError("Telegram user data missing")

    return json.loads(user_data)


# =========================================================
# TELEGRAM AUTH
# =========================================================

@app.route("/api/auth/telegram", methods=["POST"])
def telegram_auth():

    try:

        body = request.get_json(
            silent=True
        ) or {}

        user = verify_telegram_init_data(
            body.get("initData")
        )

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

@app.route("/api/deposits", methods=["POST"])
def deposits():

    try:

        body = request.get_json(
            silent=True
        ) or {}

        user = verify_telegram_init_data(
            body.get("initData")
        )

        amount = body.get("amount")
        coins = body.get("coins")
        utr = body.get("utr")

        # -------------------------------------------------
        # IMPORTANT
        # Database/storage integration will be added later.
        #
        # DO NOT credit coins here.
        # Coins must be credited only after admin approval.
        # -------------------------------------------------

        return jsonify({
            "ok": True,
            "user_id": user["id"],
            "amount": amount,
            "coins": coins,
            "utr": utr,
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

@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "ok": True,
        "service": "Cricket Card Arena"
    })


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "8000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
