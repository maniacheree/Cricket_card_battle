import os
import hmac
import hashlib
import json
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, send_from_directory, jsonify, request
import psycopg2
from psycopg2.extras import RealDictCursor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

ADMIN_ID = "7035868085"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id TEXT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    coins INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS deposits (
                    id TEXT PRIMARY KEY,
                    telegram_id TEXT NOT NULL REFERENCES users(telegram_id),
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    coins INTEGER NOT NULL CHECK (coins > 0),
                    utr TEXT NOT NULL,
                    screenshot BYTEA NOT NULL,
                    screenshot_mime TEXT NOT NULL,
                    screenshot_name TEXT,
                    status TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','APPROVED','REJECTED')),
                    admin_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    reviewed_at TIMESTAMPTZ
                )
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS deposits_utr_unique
                ON deposits (LOWER(utr))
            """)
            conn.commit()
    finally:
        conn.close()


def telegram_auth(raw_init_data):
    """Validate Telegram Web App initData server-side."""
    if not BOT_TOKEN or not raw_init_data:
        return None

    from urllib.parse import parse_qsl

    try:
        pairs = dict(parse_qsl(raw_init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{k}={pairs[k]}" for k in sorted(pairs)
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        user_json = pairs.get("user")
        if not user_json:
            return None

        user = json.loads(user_json)
        if not user.get("id"):
            return None

        return user
    except Exception:
        return None


def require_telegram(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = telegram_auth(request.headers.get("X-Telegram-Init-Data", ""))
        if not user:
            return jsonify({"ok": False, "error": "Telegram authentication required"}), 401
        request.telegram_user = user
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = telegram_auth(request.headers.get("X-Telegram-Init-Data", ""))
        if not user:
            return jsonify({"ok": False, "error": "Telegram authentication required"}), 401
        if str(user.get("id")) != ADMIN_ID:
            return jsonify({"ok": False, "error": "Admin access denied"}), 403
        request.telegram_user = user
        return fn(*args, **kwargs)
    return wrapper


@app.before_request
def ensure_db():
    # Create tables lazily so a temporary DB connection issue doesn't
    # prevent the Flask process itself from starting.
    if request.path in ("/health", "/"):
        return
    try:
        init_db()
    except Exception:
        pass


@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/admin")
def admin():
    return send_from_directory(BASE_DIR, "admin.html")


@app.get("/health")
def health():
    try:
        init_db()
        return jsonify({"ok": True, "service": "Cricket Card Arena", "database": True})
    except Exception as e:
        return jsonify({"ok": True, "service": "Cricket Card Arena", "database": False, "error": str(e)}), 200


@app.post("/api/me")
@require_telegram
def upsert_me():
    u = request.telegram_user
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO users (telegram_id, username, first_name, last_name)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    username=EXCLUDED.username,
                    first_name=EXCLUDED.first_name,
                    last_name=EXCLUDED.last_name,
                    updated_at=NOW()
                RETURNING telegram_id, username, first_name, last_name, coins
            """, (
                str(u["id"]),
                u.get("username"),
                u.get("first_name"),
                u.get("last_name")
            ))
            row = cur.fetchone()
            conn.commit()
            return jsonify({"ok": True, "user": row})
    finally:
        conn.close()


@app.get("/api/deposits")
@require_telegram
def my_deposits():
    uid = str(request.telegram_user["id"])
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, amount, coins, utr, status, created_at, reviewed_at
                FROM deposits
                WHERE telegram_id=%s
                ORDER BY created_at DESC
                LIMIT 50
            """, (uid,))
            rows = cur.fetchall()
            for r in rows:
                if r["created_at"]:
                    r["created_at"] = r["created_at"].isoformat()
                if r["reviewed_at"]:
                    r["reviewed_at"] = r["reviewed_at"].isoformat()
            return jsonify({"ok": True, "deposits": rows})
    finally:
        conn.close()


@app.post("/api/deposits")
@require_telegram
def create_deposit():
    uid = str(request.telegram_user["id"])

    try:
        amount = int(request.form.get("amount", "0"))
    except ValueError:
        amount = 0

    utr = request.form.get("utr", "").strip()
    screenshot = request.files.get("screenshot")

    if amount <= 0:
        return jsonify({"ok": False, "error": "Enter a valid payment amount"}), 400
    if not utr:
        return jsonify({"ok": False, "error": "UTR / transaction ID is required"}), 400
    if not screenshot:
        return jsonify({"ok": False, "error": "Payment screenshot is required"}), 400

    if not screenshot.mimetype.startswith("image/"):
        return jsonify({"ok": False, "error": "Screenshot must be an image"}), 400

    data = screenshot.read()
    if len(data) > 10 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Screenshot must be under 10 MB"}), 400

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (telegram_id, username, first_name, last_name)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (telegram_id) DO NOTHING
            """, (
                uid,
                request.telegram_user.get("username"),
                request.telegram_user.get("first_name"),
                request.telegram_user.get("last_name")
            ))

            deposit_id = "DEP-" + uuid.uuid4().hex[:12].upper()

            cur.execute("""
                INSERT INTO deposits
                    (id, telegram_id, amount, coins, utr, screenshot,
                     screenshot_mime, screenshot_name)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id, amount, coins, utr, status, created_at
            """, (
                deposit_id, uid, amount, amount, utr, psycopg2.Binary(data),
                screenshot.mimetype, screenshot.filename
            ))
            row = cur.fetchone()
            conn.commit()

            return jsonify({
                "ok": True,
                "message": "Deposit submitted • Waiting for admin approval",
                "deposit": {
                    "id": row[0],
                    "amount": row[1],
                    "coins": row[2],
                    "utr": row[3],
                    "status": row[4],
                    "created_at": row[5].isoformat()
                }
            })
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"ok": False, "error": "This UTR / transaction ID has already been submitted"}), 409
    finally:
        conn.close()


@app.get("/api/admin/stats")
@require_admin
def admin_stats():
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS total_users, COALESCE(SUM(coins),0) AS total_coins FROM users")
            u = cur.fetchone()
            cur.execute("SELECT COUNT(*) AS pending_deposits FROM deposits WHERE status='PENDING'")
            d = cur.fetchone()
            return jsonify({
                "ok": True,
                "total_users": int(u["total_users"] or 0),
                "total_coins": int(u["total_coins"] or 0),
                "pending_deposits": int(d["pending_deposits"] or 0)
            })
    finally:
        conn.close()


@app.get("/api/admin/users")
@require_admin
def admin_users():
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT u.telegram_id, u.username, u.first_name, u.last_name, u.coins,
                       u.created_at, u.updated_at,
                       COUNT(d.id) AS deposit_count
                FROM users u
                LEFT JOIN deposits d ON d.telegram_id=u.telegram_id
                GROUP BY u.telegram_id
                ORDER BY u.updated_at DESC NULLS LAST, u.created_at DESC
                LIMIT 500
            """)
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if r[k]: r[k] = r[k].isoformat()
            return jsonify({"ok": True, "users": rows})
    finally:
        conn.close()


@app.get("/api/admin/deposits")
@require_admin
def admin_deposits():
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT d.id, d.telegram_id, d.amount, d.coins, d.utr,
                       d.status, d.screenshot_name, d.screenshot_mime,
                       d.created_at, d.reviewed_at,
                       u.username, u.first_name, u.last_name, u.coins AS user_coins
                FROM deposits d
                JOIN users u ON u.telegram_id=d.telegram_id
                ORDER BY CASE WHEN d.status='PENDING' THEN 0 ELSE 1 END,
                         d.created_at DESC
                LIMIT 200
            """)
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "reviewed_at"):
                    if r[k]:
                        r[k] = r[k].isoformat()
            return jsonify({"ok": True, "deposits": rows})
    finally:
        conn.close()


@app.get("/api/admin/deposits/<deposit_id>/screenshot")
@require_admin
def admin_screenshot(deposit_id):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT screenshot, screenshot_mime, screenshot_name
                FROM deposits WHERE id=%s
            """, (deposit_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({"ok": False, "error": "Deposit not found"}), 404

            from flask import Response
            return Response(
                bytes(row[0]),
                mimetype=row[1],
                headers={
                    "Content-Disposition":
                        f'inline; filename="{row[2] or "payment-screenshot"}"'
                }
            )
    finally:
        conn.close()


@app.post("/api/admin/deposits/<deposit_id>/approve")
@require_admin
def approve_deposit(deposit_id):
    admin_id = str(request.telegram_user["id"])
    conn = db()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, telegram_id, coins, status
                    FROM deposits
                    WHERE id=%s
                    FOR UPDATE
                """, (deposit_id,))
                d = cur.fetchone()

                if not d:
                    return jsonify({"ok": False, "error": "Deposit not found"}), 404
                if d["status"] != "PENDING":
                    return jsonify({
                        "ok": False,
                        "error": f"Deposit already {d['status']}"
                    }), 409

                cur.execute("""
                    UPDATE deposits
                    SET status='APPROVED', admin_id=%s, reviewed_at=NOW()
                    WHERE id=%s
                """, (admin_id, deposit_id))

                cur.execute("""
                    UPDATE users
                    SET coins=coins+%s, updated_at=NOW()
                    WHERE telegram_id=%s
                    RETURNING coins
                """, (d["coins"], d["telegram_id"]))
                new_balance = cur.fetchone()["coins"]

                return jsonify({
                    "ok": True,
                    "message": "Deposit approved and coins credited",
                    "deposit_id": deposit_id,
                    "credited_coins": d["coins"],
                    "new_balance": new_balance
                })
    finally:
        conn.close()


@app.post("/api/admin/deposits/<deposit_id>/reject")
@require_admin
def reject_deposit(deposit_id):
    admin_id = str(request.telegram_user["id"])
    conn = db()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, status
                    FROM deposits
                    WHERE id=%s
                    FOR UPDATE
                """, (deposit_id,))
                d = cur.fetchone()

                if not d:
                    return jsonify({"ok": False, "error": "Deposit not found"}), 404
                if d["status"] != "PENDING":
                    return jsonify({
                        "ok": False,
                        "error": f"Deposit already {d['status']}"
                    }), 409

                cur.execute("""
                    UPDATE deposits
                    SET status='REJECTED', admin_id=%s, reviewed_at=NOW()
                    WHERE id=%s
                """, (admin_id, deposit_id))

                return jsonify({
                    "ok": True,
                    "message": "Deposit rejected",
                    "deposit_id": deposit_id
                })
    finally:
        conn.close()


@app.get("/<path:filename>")
def static_files(filename):
    if filename.startswith("api/"):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080"))
    )
