import os
import hmac
import hashlib
import json
import uuid
import random
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, send_from_directory, jsonify, request
import psycopg2
from psycopg2.extras import RealDictCursor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

ADMIN_ID = "7035868085"
REQUIRED_CHANNEL = "@Cricketcardarena"
REQUIRED_GROUP_ID = os.environ.get("REQUIRED_GROUP_ID", "").strip()
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
                    locked_coins INTEGER NOT NULL DEFAULT 0,
                    referred_by TEXT,
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
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id TEXT PRIMARY KEY,
                    telegram_id TEXT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                    coins INTEGER NOT NULL CHECK (coins > 0),
                    upi_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','APPROVED','REJECTED')),
                    admin_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    reviewed_at TIMESTAMPTZ
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS withdrawals_user_idx ON withdrawals (telegram_id, created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS withdrawals_status_idx ON withdrawals (status, created_at DESC)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_cards (
                    id TEXT PRIMARY KEY,
                    telegram_id TEXT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                    card_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS user_cards_user_idx
                ON user_cards (telegram_id, created_at DESC)
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS deposits_utr_unique
                ON deposits (LOWER(utr))
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS battle_queue (
                    telegram_id TEXT PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
                    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS battle_queue_time_idx ON battle_queue (queued_at)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS battles (
                    id TEXT PRIMARY KEY,
                    player1_id TEXT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                    player2_id TEXT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                    player1_card_id TEXT,
                    player2_card_id TEXT,
                    player1_power INTEGER,
                    player2_power INTEGER,
                    winner_id TEXT,
                    loser_id TEXT,
                    status TEXT NOT NULL DEFAULT 'SELECTING'
                        CHECK (status IN ('SELECTING','FINISHED','DRAW','CANCELLED')),
                    claim_slots JSONB,
                    claimed_card_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMPTZ,
                    claimed_at TIMESTAMPTZ
                )
            """)
            cur.execute("ALTER TABLE battles ADD COLUMN IF NOT EXISTS player1_cards JSONB")
            cur.execute("ALTER TABLE battles ADD COLUMN IF NOT EXISTS player2_cards JSONB")
            cur.execute("CREATE INDEX IF NOT EXISTS battles_players_idx ON battles (player1_id, player2_id, created_at DESC)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS wallet_adjustments (
                    id TEXT PRIMARY KEY,
                    telegram_id TEXT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                    admin_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('ADD','CUT')),
                    coins INTEGER NOT NULL CHECK (coins > 0),
                    balance_before INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS wallet_adjustments_user_idx ON wallet_adjustments (telegram_id, created_at DESC)")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_coins INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pack_open_count INTEGER NOT NULL DEFAULT 0")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id TEXT PRIMARY KEY,
                    referrer_id TEXT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                    referred_id TEXT NOT NULL UNIQUE REFERENCES users(telegram_id) ON DELETE CASCADE,
                    reward_coins INTEGER NOT NULL DEFAULT 2 CHECK (reward_coins > 0),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS referrals_referrer_idx ON referrals (referrer_id, created_at DESC)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    target INTEGER NOT NULL DEFAULT 1,
                    reward_coins INTEGER NOT NULL CHECK (reward_coins > 0),
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_tasks (
                    telegram_id TEXT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (telegram_id, task_id)
                )
            """)
            cur.execute("""
                INSERT INTO tasks (id,title,description,task_type,target,reward_coins) VALUES
                ('TASK_JOIN_CHANNEL','JOIN THE CHANNEL','Join the official Cricket Card Arena channel.','JOIN_CHANNEL',1,2),
                ('TASK_JOIN_GROUP','JOIN THE GROUP','Join the official Cricket Card Arena community group.','JOIN_GROUP',1,2),
                ('TASK_REFER_3','REFER 3 FRIENDS','Bring 3 successful new players to Cricket Card Arena.','REFER_COUNT',3,6),
                ('TASK_OPEN_PACK','OPEN 1 PACK','Open your first card pack.','OPEN_PACK_COUNT',1,2)
                ON CONFLICT (id) DO NOTHING
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
        user["_start_param"] = pairs.get("start_param", "")
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


def ensure_user(conn, u):
    uid = str(u["id"])
    start_param = str(u.get("_start_param") or "").strip()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            INSERT INTO users (telegram_id, username, first_name, last_name)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (telegram_id) DO UPDATE SET
                username=EXCLUDED.username, first_name=EXCLUDED.first_name,
                last_name=EXCLUDED.last_name, updated_at=NOW()
            RETURNING telegram_id, username, first_name, last_name, coins, locked_coins, referred_by, pack_open_count, (xmax = 0) AS inserted
        """, (uid, u.get("username"), u.get("first_name"), u.get("last_name")))
        row = cur.fetchone()

        # A referral is credited only once, only for a newly created user, and never to self.
        if start_param.startswith("ref_") and row.get("inserted") and not row.get("referred_by"):
            referrer_id = start_param[4:].strip()
            if referrer_id and referrer_id != uid:
                cur.execute("SELECT telegram_id FROM users WHERE telegram_id=%s", (referrer_id,))
                ref = cur.fetchone()
                if ref:
                    cur.execute("SELECT 1 FROM referrals WHERE referred_id=%s", (uid,))
                    if not cur.fetchone():
                        cur.execute("UPDATE users SET referred_by=%s, updated_at=NOW() WHERE telegram_id=%s", (referrer_id, uid))
                        cur.execute("UPDATE users SET locked_coins=locked_coins+2, updated_at=NOW() WHERE telegram_id=%s", (referrer_id,))
                        cur.execute("INSERT INTO referrals (id,referrer_id,referred_id,reward_coins) VALUES (%s,%s,%s,2)", ("REF-"+uuid.uuid4().hex[:12].upper(), referrer_id, uid))
        cur.execute("SELECT telegram_id, username, first_name, last_name, coins, locked_coins, referred_by, pack_open_count FROM users WHERE telegram_id=%s", (uid,))
        return cur.fetchone()

def load_card_pool():
    try:
        with open(os.path.join(BASE_DIR, "players.json"), "r", encoding="utf-8") as f:
            data=json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def pack_config(pack):
    # Pack price is the hard ceiling for the TOTAL value of all 3 cards.
    # The first 90% of openings are deliberately below 90% of the pack price.
    # The remaining 10% are lucky rolls, but still stay below the pack price.
    return {
        "COMMON": {"price":30, "pool_min":1, "pool_max":3},
        "RARE": {"price":75, "pool_min":2, "pool_max":4},
        "EPIC": {"price":150, "pool_min":2, "pool_max":5},
        "LEGENDARY": {"price":300, "pool_min":3, "pool_max":5},
        "ULTIMATE": {"price":600, "pool_min":5, "pool_max":6},
    }.get(pack)

def rarity_rank(r):
    return {"COMMON":1,"RARE":2,"EPIC":3,"LEGENDARY":4,"ULTIMATE":5,"ICON":6}.get(str(r).upper(),1)

def make_pack_cards(pack):
    cfg=pack_config(pack)
    pool=load_card_pool()
    if not cfg or not pool:
        raise RuntimeError("Player database unavailable")

    eligible=[x for x in pool if cfg["pool_min"] <= rarity_rank(x.get("rarity")) <= cfg["pool_max"]]
    if not eligible:
        eligible=pool

    price=int(cfg["price"])
    # 90% of openings: total reward is 45%-90% of the purchase price.
    # 10% of openings: lucky reward is 90%-99% of the purchase price.
    # In BOTH cases total card value is strictly below the pack price.
    is_lucky = random.random() >= 0.90
    target_min = max(3, int(price * (0.90 if is_lucky else 0.45)))
    target_max = max(target_min, int(price * (0.99 if is_lucky else 0.90)))
    target_total = random.randint(target_min, target_max)

    # Split the target total across exactly 3 cards. Each card is positive and
    # the sum is guaranteed to remain below the pack purchase price.
    a = random.randint(1, max(1, target_total - 2))
    b = random.randint(1, max(1, target_total - a - 1))
    c = target_total - a - b
    values=[a,b,c]
    random.shuffle(values)

    cards=[]
    for value in values:
        base=random.choice(eligible)
        card={**base, "value":int(value), "rarity":pack, "id":"CARD-"+uuid.uuid4().hex[:12].upper()}
        cards.append(card)
    return cards


@app.get("/api/referrals")
@require_telegram
def get_referrals():
    uid=str(request.telegram_user["id"])
    conn=db()
    try:
        ensure_user(conn, request.telegram_user)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS count FROM referrals WHERE referrer_id=%s", (uid,))
            count=int(cur.fetchone()["count"] or 0)
            cur.execute("SELECT locked_coins FROM users WHERE telegram_id=%s", (uid,))
            locked=int(cur.fetchone()["locked_coins"] or 0)
        conn.commit()
        return jsonify({"ok":True,"referral_code":"ref_"+uid,"referral_link":"https://t.me/CricketCardArenaBot?startapp=ref_"+uid,"successful_referrals":count,"locked_coins":locked,"reward_per_referral":2})
    finally: conn.close()

@app.get("/api/tasks")
@require_telegram
def get_tasks():
    uid=str(request.telegram_user["id"])
    conn=db()
    try:
        ensure_user(conn, request.telegram_user)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT t.id,t.title,t.description,t.task_type,t.target,t.reward_coins,t.active,
                         EXISTS(SELECT 1 FROM user_tasks ut WHERE ut.task_id=t.id AND ut.telegram_id=%s) AS completed
                         FROM tasks t WHERE t.active=TRUE ORDER BY t.created_at ASC""", (uid,))
            rows=cur.fetchall()
        conn.commit()
        return jsonify({"ok":True,"tasks":rows})
    finally: conn.close()

def task_is_complete(cur, task, uid):
    typ=task["task_type"]
    target=int(task["target"] or 1)
    if typ == "JOIN_CHANNEL":
        if not BOT_TOKEN: return False
        try:
            qs=urlencode({"chat_id":REQUIRED_CHANNEL,"user_id":uid})
            req=Request("https://api.telegram.org/bot%s/getChatMember?%s"%(BOT_TOKEN,qs),method="GET")
            with urlopen(req,timeout=8) as resp: data=json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"): return False
            m=data.get("result",{}); st=m.get("status")
            return st in ("creator","administrator","member") or (st=="restricted" and bool(m.get("is_member")))
        except Exception: return False
    if typ == "JOIN_GROUP":
        if not BOT_TOKEN or not REQUIRED_GROUP_ID: return False
        try:
            qs=urlencode({"chat_id":REQUIRED_GROUP_ID,"user_id":uid})
            req=Request("https://api.telegram.org/bot%s/getChatMember?%s"%(BOT_TOKEN,qs),method="GET")
            with urlopen(req,timeout=8) as resp: data=json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"): return False
            m=data.get("result",{}); st=m.get("status")
            return st in ("creator","administrator","member") or (st=="restricted" and bool(m.get("is_member")))
        except Exception: return False
    if typ == "REFER_COUNT":
        cur.execute("SELECT COUNT(*) AS c FROM referrals WHERE referrer_id=%s", (uid,))
        return int(cur.fetchone()["c"] or 0) >= target
    if typ == "OPEN_PACK_COUNT":
        cur.execute("SELECT pack_open_count FROM users WHERE telegram_id=%s", (uid,))
        return int(cur.fetchone()["pack_open_count"] or 0) >= target
    return False

@app.post("/api/tasks/<task_id>/claim")
@require_telegram
def claim_task(task_id):
    uid=str(request.telegram_user["id"]); conn=db()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                ensure_user(conn, request.telegram_user)
                cur.execute("SELECT * FROM tasks WHERE id=%s AND active=TRUE FOR UPDATE", (task_id,)); task=cur.fetchone()
                if not task: return jsonify({"ok":False,"error":"Task not found"}),404
                cur.execute("SELECT 1 FROM user_tasks WHERE telegram_id=%s AND task_id=%s", (uid,task_id))
                if cur.fetchone(): return jsonify({"ok":False,"error":"Task already claimed"}),400
                if not task_is_complete(cur,task,uid): return jsonify({"ok":False,"error":"Task requirement not completed yet"}),400
                reward=int(task["reward_coins"])
                cur.execute("UPDATE users SET locked_coins=locked_coins+%s,updated_at=NOW() WHERE telegram_id=%s RETURNING locked_coins", (reward,uid))
                bal=int(cur.fetchone()["locked_coins"])
                cur.execute("INSERT INTO user_tasks (telegram_id,task_id) VALUES (%s,%s)", (uid,task_id))
                return jsonify({"ok":True,"reward":reward,"locked_coins":bal})
    finally: conn.close()

@app.get("/api/membership")
@require_telegram
def membership():
    uid = str(request.telegram_user["id"])
    if not BOT_TOKEN:
        return jsonify({"ok": False, "error": "Bot token is not configured"}), 503
    def check_member(chat_id):
        try:
            qs = urlencode({"chat_id": chat_id, "user_id": uid})
            req = Request("https://api.telegram.org/bot%s/getChatMember?%s" % (BOT_TOKEN, qs), method="GET")
            with urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                return False, data.get("description", "Telegram membership check failed")
            m = data.get("result", {})
            status = m.get("status")
            joined = status in ("creator", "administrator", "member") or (status == "restricted" and bool(m.get("is_member")))
            return joined, status
        except Exception as e:
            return False, str(e)
    channel_ok, channel_detail = check_member(REQUIRED_CHANNEL)
    if not REQUIRED_GROUP_ID:
        return jsonify({"ok": False, "configured": False, "error": "REQUIRED_GROUP_ID is not configured"}), 503
    group_ok, group_detail = check_member(REQUIRED_GROUP_ID)
    return jsonify({"ok": True, "configured": True, "channel": {"joined": channel_ok, "detail": channel_detail}, "group": {"joined": group_ok, "detail": group_detail}, "can_enter": channel_ok and group_ok})

@app.get("/api/state")
@require_telegram
def get_state():
    uid=str(request.telegram_user["id"])
    conn=db()
    try:
        ensure_user(conn, request.telegram_user)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT telegram_id, username, first_name, last_name, coins, locked_coins, pack_open_count FROM users WHERE telegram_id=%s", (uid,))
            user=cur.fetchone()
            cur.execute("SELECT card_json FROM user_cards WHERE telegram_id=%s ORDER BY created_at DESC", (uid,))
            cards=[r["card_json"] for r in cur.fetchall()]
        conn.commit()
        return jsonify({"ok":True,"user":user,"cards":cards})
    finally:
        conn.close()

@app.post("/api/packs/open")
@require_telegram
def open_pack_server():
    uid=str(request.telegram_user["id"])
    pack=str(request.json.get("pack","")).upper() if request.is_json else str(request.form.get("pack","")).upper()
    cfg=pack_config(pack)
    if not cfg:
        return jsonify({"ok":False,"error":"Invalid pack"}),400
    conn=db()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                ensure_user(conn, request.telegram_user)
                cur.execute("SELECT coins, locked_coins FROM users WHERE telegram_id=%s FOR UPDATE", (uid,))
                u=cur.fetchone()
                normal=int(u["coins"] or 0)
                locked=int(u["locked_coins"] or 0)
                # Referral/task coins are usable only for packs, in 30-coin blocks.
                locked_use=min(cfg["price"], (locked//30)*30)
                normal_use=cfg["price"]-locked_use
                if normal < normal_use:
                    return jsonify({"ok":False,"error":f"Need {normal_use} normal coins + {locked_use} locked coins for this pack"}),400
                new_cards=make_pack_cards(pack)
                cur.execute("""UPDATE users SET coins=coins-%s, locked_coins=locked_coins-%s, pack_open_count=pack_open_count+1, updated_at=NOW() WHERE telegram_id=%s RETURNING coins, locked_coins""", (normal_use,locked_use,uid))
                balances=cur.fetchone()
                new_coins=int(balances["coins"]); new_locked=int(balances["locked_coins"])
                for c in new_cards:
                    cur.execute("INSERT INTO user_cards (id,telegram_id,card_json) VALUES (%s,%s,%s)", (c["id"],uid,json.dumps(c)))
                return jsonify({"ok":True,"coins":new_coins,"locked_coins":new_locked,"used_locked_coins":locked_use,"used_normal_coins":normal_use,"cards":new_cards})
    finally:
        conn.close()


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



@app.get("/api/withdrawals")
@require_telegram
def get_withdrawals():
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, coins, upi_id, status, created_at, reviewed_at FROM withdrawals WHERE telegram_id=%s ORDER BY created_at DESC LIMIT 30", (str(request.telegram_user["id"]),))
            return jsonify({"ok": True, "withdrawals": cur.fetchall()})
    finally:
        conn.close()

@app.post("/api/withdrawals")
@require_telegram
def create_withdrawal():
    data = request.get_json(silent=True) or {}
    try: coins = int(data.get("coins", 0))
    except Exception: coins = 0
    upi_id = str(data.get("upi_id", "")).strip()
    if coins <= 0: return jsonify({"ok": False, "error": "Enter a valid coin amount"}), 400
    if not upi_id or len(upi_id) > 120: return jsonify({"ok": False, "error": "Enter a valid UPI ID"}), 400
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            uid = str(request.telegram_user["id"])
            cur.execute("SELECT coins FROM users WHERE telegram_id=%s FOR UPDATE", (uid,))
            u = cur.fetchone()
            if not u: return jsonify({"ok": False, "error": "User account not found"}), 404
            balance = int(u["coins"] or 0)
            if coins > balance: return jsonify({"ok": False, "error": "Insufficient coins"}), 400
            wid = "WD-" + uuid.uuid4().hex[:12].upper()
            cur.execute("UPDATE users SET coins=coins-%s, updated_at=NOW() WHERE telegram_id=%s", (coins, uid))
            cur.execute("INSERT INTO withdrawals (id, telegram_id, coins, upi_id, status) VALUES (%s,%s,%s,%s,'PENDING')", (wid, uid, coins, upi_id))
            conn.commit()
            return jsonify({"ok": True, "id": wid, "status": "PENDING", "coins": coins, "balance": balance-coins})
    except Exception:
        conn.rollback(); raise
    finally: conn.close()

@app.get("/api/admin/withdrawals")
@require_admin
def admin_withdrawals():
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT w.id,w.telegram_id,w.coins,w.upi_id,w.status,w.created_at,w.reviewed_at,u.username,u.first_name,u.last_name FROM withdrawals w JOIN users u ON u.telegram_id=w.telegram_id ORDER BY w.created_at DESC LIMIT 200""")
            return jsonify({"ok": True, "withdrawals": cur.fetchall()})
    finally: conn.close()

@app.post("/api/admin/withdrawals/<withdrawal_id>/approve")
@require_admin
def approve_withdrawal(withdrawal_id):
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id,status,coins FROM withdrawals WHERE id=%s FOR UPDATE", (withdrawal_id,)); row=cur.fetchone()
            if not row: return jsonify({"ok":False,"error":"Withdrawal not found"}),404
            if row["status"] != "PENDING": return jsonify({"ok":False,"error":"Withdrawal already reviewed"}),400
            cur.execute("UPDATE withdrawals SET status='APPROVED',admin_id=%s,reviewed_at=NOW() WHERE id=%s",(str(request.telegram_user["id"]),withdrawal_id)); conn.commit()
            return jsonify({"ok":True,"status":"APPROVED","coins":int(row["coins"])})
    except Exception:
        conn.rollback(); raise
    finally: conn.close()

@app.post("/api/admin/withdrawals/<withdrawal_id>/reject")
@require_admin
def reject_withdrawal(withdrawal_id):
    conn=db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id,status,coins,telegram_id FROM withdrawals WHERE id=%s FOR UPDATE",(withdrawal_id,)); row=cur.fetchone()
            if not row: return jsonify({"ok":False,"error":"Withdrawal not found"}),404
            if row["status"] != "PENDING": return jsonify({"ok":False,"error":"Withdrawal already reviewed"}),400
            cur.execute("UPDATE users SET coins=coins+%s,updated_at=NOW() WHERE telegram_id=%s",(int(row["coins"]),row["telegram_id"]))
            cur.execute("UPDATE withdrawals SET status='REJECTED',admin_id=%s,reviewed_at=NOW() WHERE id=%s",(str(request.telegram_user["id"]),withdrawal_id)); conn.commit()
            return jsonify({"ok":True,"status":"REJECTED","refunded_coins":int(row["coins"])})
    except Exception:
        conn.rollback(); raise
    finally: conn.close()

@app.post('/api/cards/sell')
@require_telegram
def sell_card_server():
    uid=str(request.telegram_user['id'])
    data=request.get_json(silent=True) or request.form
    card_id=str(data.get('card_id','')).strip()
    if not card_id:
        return jsonify({'ok':False,'error':'Card ID required'}),400
    conn=db()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # A card involved in a live match or pending hidden-card claim cannot be sold.
                cur.execute("""
                    SELECT 1 FROM battles
                    WHERE status='SELECTING' AND
                          ((player1_id=%s AND player1_card_id=%s) OR (player2_id=%s AND player2_card_id=%s))
                    LIMIT 1
                """,(uid,card_id,uid,card_id))
                if cur.fetchone(): return jsonify({'ok':False,'error':'Card is locked for the active battle'}),409
                cur.execute("""
                    SELECT 1 FROM battles
                    WHERE status='FINISHED' AND winner_id=%s AND claimed_card_id IS NULL
                      AND claim_slots @> %s::jsonb
                    LIMIT 1
                """,(uid,json.dumps([card_id])))
                if cur.fetchone():
                    return jsonify({'ok':False,'error':'Finish your battle card claim first'}),409
                cur.execute('SELECT card_json FROM user_cards WHERE id=%s AND telegram_id=%s FOR UPDATE',(card_id,uid))
                row=cur.fetchone()
                if not row:return jsonify({'ok':False,'error':'Card not found in your collection'}),404
                card=row['card_json']; value=int(card.get('value',0) or 0)
                cur.execute('DELETE FROM user_cards WHERE id=%s AND telegram_id=%s',(card_id,uid))
                cur.execute('UPDATE users SET coins=coins+%s,updated_at=NOW() WHERE telegram_id=%s RETURNING coins',(value,uid))
                coins=int(cur.fetchone()['coins'])
                return jsonify({'ok':True,'coins':coins,'sold_value':value})
    finally: conn.close()


def battle_card_power(card):
    def n(k):
        try: return int(card.get(k,0) or 0)
        except Exception: return 0
    return n("bat") + n("bowl") + n("field")

def team_power(cards):
    return sum(battle_card_power(c) for c in cards)

def get_active_battle(cur, uid):
    cur.execute("""
        SELECT id, player1_id, player2_id, player1_cards, player2_cards,
               player1_power, player2_power, winner_id, loser_id, status,
               claim_slots, claimed_card_id, created_at, finished_at, claimed_at
        FROM battles
        WHERE status IN ('SELECTING','FINISHED')
          AND (player1_id=%s OR player2_id=%s)
        ORDER BY created_at DESC LIMIT 1
    """, (uid,uid))
    return cur.fetchone()

def battle_payload(cur, b, uid):
    if not b: return {"ok":True,"battle":None}
    p1=str(b["player1_id"]); p2=str(b["player2_id"])
    oid=p2 if uid==p1 else p1
    cur.execute("SELECT telegram_id, username, first_name, last_name FROM users WHERE telegram_id=%s",(oid,))
    o=cur.fetchone()
    mine_cards=b.get("player1_cards") if uid==p1 else b.get("player2_cards")
    opp_cards=b.get("player2_cards") if uid==p1 else b.get("player1_cards")
    mine_cards=mine_cards or []
    opp_cards=opp_cards or []
    mine_power=b["player1_power"] if uid==p1 else b["player2_power"]
    opp_power=b["player2_power"] if uid==p1 else b["player1_power"]
    is_win=str(b.get("winner_id") or "")==uid
    slots=b.get("claim_slots") or []
    return {"ok":True,"battle":{
        "id":b["id"],"status":b["status"],
        "opponent":{"id":oid,"username":o["username"] if o else None,"first_name":o["first_name"] if o else None,"last_name":o["last_name"] if o else None},
        "my_cards_selected":len(mine_cards)==3,"my_card_count":len(mine_cards),
        "opponent_cards_selected":len(opp_cards)==3,"opponent_card_count":len(opp_cards),
        "my_power":mine_power,"opponent_power":(opp_power if b["status"] in ('FINISHED','DRAW') else None),
        "winner":is_win,"loser":bool(b.get("loser_id")) and str(b.get("loser_id"))==uid,
        "can_claim":bool(is_win and b["status"]=='FINISHED' and not b.get("claimed_card_id")),
        "claim_count":len(slots) if is_win and b["status"]=='FINISHED' else 0,
        "claimed":bool(b.get("claimed_card_id"))
    }}

@app.post('/api/battle/find')
@require_telegram
def battle_find():
    uid=str(request.telegram_user['id'])
    conn=db()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                ensure_user(conn,request.telegram_user)
                cur.execute('SELECT COUNT(*) AS n FROM user_cards WHERE telegram_id=%s',(uid,))
                if int(cur.fetchone()['n'])<1:
                    return jsonify({'ok':False,'error':'Open at least 1 pack/card before playing'}),400
                selecting=get_active_battle(cur,uid)
                if selecting and selecting.get('status')=='SELECTING':
                    return jsonify(battle_payload(cur,selecting,uid))
                cur.execute("""SELECT * FROM battles
                    WHERE status='FINISHED' AND winner_id=%s AND claimed_card_id IS NULL
                    ORDER BY finished_at DESC LIMIT 1
                """,(uid,))
                pending_claim=cur.fetchone()
                if pending_claim:
                    return jsonify(battle_payload(cur,pending_claim,uid))
                cur.execute("""INSERT INTO battle_queue (telegram_id) VALUES (%s)
                    ON CONFLICT (telegram_id) DO UPDATE SET last_seen=NOW()""",(uid,))
                cur.execute("""SELECT telegram_id FROM battle_queue
                    WHERE telegram_id<>%s AND last_seen>NOW()-INTERVAL '20 seconds'
                    ORDER BY queued_at ASC FOR UPDATE SKIP LOCKED LIMIT 1""",(uid,))
                opp=cur.fetchone()
                if not opp:
                    return jsonify({'ok':True,'searching':True,'battle':None,'message':'Searching for an online opponent...'})
                oid=str(opp['telegram_id'])
                cur.execute('DELETE FROM battle_queue WHERE telegram_id IN (%s,%s)',(uid,oid))
                bid='BAT-'+uuid.uuid4().hex[:12].upper()
                cur.execute("INSERT INTO battles (id,player1_id,player2_id,status) VALUES (%s,%s,%s,'SELECTING')",(bid,uid,oid))
                cur.execute("SELECT * FROM battles WHERE id=%s",(bid,))
                out=battle_payload(cur,cur.fetchone(),uid); out['searching']=False; out['message']='Opponent found. Choose exactly 3 cards.'
                return jsonify(out)
    finally: conn.close()

@app.post('/api/battle/select')
@require_telegram
def battle_select():
    uid=str(request.telegram_user['id'])
    data=request.get_json(silent=True) or request.form
    raw=data.get('card_ids') if hasattr(data,'get') else None
    if isinstance(raw,str):
        try: raw=json.loads(raw)
        except Exception: raw=[x.strip() for x in raw.split(',') if x.strip()]
    card_ids=raw or []
    if not isinstance(card_ids,list) or len(card_ids)!=3:
        return jsonify({'ok':False,'error':'Choose exactly 3 cards'}),400
    card_ids=[str(x).strip() for x in card_ids]
    if len(set(card_ids))!=3 or any(not x for x in card_ids):
        return jsonify({'ok':False,'error':'Choose 3 different cards'}),400
    conn=db()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                b=get_active_battle(cur,uid)
                if not b or b['status']!='SELECTING':
                    return jsonify({'ok':False,'error':'No active card-selection battle'}),400
                is_p1=str(b['player1_id'])==uid
                cfield='player1_cards' if is_p1 else 'player2_cards'
                pfield='player1_power' if is_p1 else 'player2_power'
                if b.get(cfield):
                    return jsonify({'ok':False,'error':'Your 3 cards are already locked'}),409
                cur.execute('SELECT id,card_json FROM user_cards WHERE id=ANY(%s) AND telegram_id=%s FOR UPDATE',(card_ids,uid))
                rows=cur.fetchall()
                found={str(r['id']):r['card_json'] for r in rows}
                if len(found)!=3:
                    return jsonify({'ok':False,'error':'One or more selected cards are not in your collection'}),400
                ordered=[found[x] for x in card_ids]
                power=team_power(ordered)
                cur.execute(f'UPDATE battles SET {cfield}=%s,{pfield}=%s WHERE id=%s',(json.dumps(card_ids),power,b['id']))
                cur.execute('SELECT * FROM battles WHERE id=%s FOR UPDATE',(b['id'],)); b=cur.fetchone()
                p1cards=b.get('player1_cards') or []; p2cards=b.get('player2_cards') or []
                if len(p1cards)==3 and len(p2cards)==3 and b['status']=='SELECTING':
                    a=int(b['player1_power'] or 0); z=int(b['player2_power'] or 0)
                    if a==z:
                        cur.execute("UPDATE battles SET status='DRAW',finished_at=NOW() WHERE id=%s",(b['id'],))
                    else:
                        win=str(b['player1_id']) if a>z else str(b['player2_id'])
                        lose=str(b['player2_id']) if win==str(b['player1_id']) else str(b['player1_id'])
                        loser_cards=p2cards if lose==str(b['player2_id']) else p1cards
                        # Exactly the 3 cards selected by the loser, shuffled only for blind-choice positions.
                        slots=list(loser_cards); random.shuffle(slots)
                        cur.execute("UPDATE battles SET status='FINISHED',winner_id=%s,loser_id=%s,claim_slots=%s,finished_at=NOW() WHERE id=%s",(win,lose,json.dumps(slots),b['id']))
                cur.execute('SELECT * FROM battles WHERE id=%s',(b['id'],)); return jsonify(battle_payload(cur,cur.fetchone(),uid))
    finally: conn.close()

@app.get('/api/battle/status')
@require_telegram
def battle_status():
    uid=str(request.telegram_user['id']); conn=db()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('UPDATE battle_queue SET last_seen=NOW() WHERE telegram_id=%s',(uid,))
                return jsonify(battle_payload(cur,get_active_battle(cur,uid),uid))
    finally: conn.close()

@app.post('/api/battle/cancel')
@require_telegram
def battle_cancel():
    uid=str(request.telegram_user['id']); conn=db()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM battle_queue WHERE telegram_id=%s',(uid,))
                cur.execute("UPDATE battles SET status='CANCELLED' WHERE status='SELECTING' AND (player1_id=%s OR player2_id=%s)",(uid,uid))
                return jsonify({'ok':True})
    finally: conn.close()

@app.post('/api/battle/claim')
@require_telegram
def battle_claim():
    uid=str(request.telegram_user['id']); data=request.get_json(silent=True) or request.form
    try: slot=int(data.get('slot',-1))
    except Exception: slot=-1
    conn=db()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                b=get_active_battle(cur,uid)
                if not b or b['status']!='FINISHED' or str(b.get('winner_id'))!=uid:return jsonify({'ok':False,'error':'Card claim is not available'}),400
                if b.get('claimed_card_id'):return jsonify({'ok':False,'error':'Reward already claimed'}),409
                slots=b.get('claim_slots') or []
                if slot<0 or slot>=len(slots):return jsonify({'ok':False,'error':'Invalid card choice'}),400
                cid=str(slots[slot]); loser=str(b['loser_id'])
                cur.execute('SELECT id,card_json FROM user_cards WHERE id=%s AND telegram_id=%s FOR UPDATE',(cid,loser))
                row=cur.fetchone()
                if not row:return jsonify({'ok':False,'error':'That hidden card is no longer available'}),409
                cur.execute('UPDATE user_cards SET telegram_id=%s WHERE id=%s AND telegram_id=%s',(uid,cid,loser))
                cur.execute('UPDATE battles SET claimed_card_id=%s,claimed_at=NOW() WHERE id=%s AND claimed_card_id IS NULL',(cid,b['id']))
                return jsonify({'ok':True,'battle_id':b['id'],'card':row['card_json'],'message':'You won and claimed 1 card from your opponent'})
    finally: conn.close()

@app.get('/api/admin/battles')
@require_admin
def admin_battles():
    conn=db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT b.id,b.player1_id,b.player2_id,b.player1_power,b.player2_power,b.winner_id,b.loser_id,b.status,b.claimed_card_id,b.created_at,b.finished_at,b.claimed_at,
                u1.username AS player1_username,u1.first_name AS player1_first_name,u2.username AS player2_username,u2.first_name AS player2_first_name
                FROM battles b JOIN users u1 ON u1.telegram_id=b.player1_id JOIN users u2 ON u2.telegram_id=b.player2_id ORDER BY b.created_at DESC LIMIT 500""")
            rows=cur.fetchall()
            for r in rows:
                for k in ('created_at','finished_at','claimed_at'):
                    if r[k]:r[k]=r[k].isoformat()
            return jsonify({'ok':True,'battles':rows})
    finally: conn.close()


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


@app.post("/api/admin/wallet/adjust")
@require_admin
def admin_wallet_adjust():
    data = request.get_json(silent=True) or {}
    uid = str(data.get("telegram_id") or "").strip()
    action = str(data.get("action") or "").upper().strip()
    reason = str(data.get("reason") or "").strip()[:300]
    try:
        amount = int(data.get("coins"))
    except (TypeError, ValueError):
        amount = 0
    if not uid:
        return jsonify({"ok": False, "error": "Telegram User ID is required"}), 400
    if action not in ("ADD", "CUT"):
        return jsonify({"ok": False, "error": "Action must be ADD or CUT"}), 400
    if amount <= 0:
        return jsonify({"ok": False, "error": "Coins must be greater than 0"}), 400
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT coins FROM users WHERE telegram_id=%s FOR UPDATE", (uid,))
            user = cur.fetchone()
            if not user:
                return jsonify({"ok": False, "error": "User not found"}), 404
            before = int(user["coins"] or 0)
            after = before + amount if action == "ADD" else before - amount
            if after < 0:
                return jsonify({"ok": False, "error": f"Insufficient balance. Current balance: {before} coins"}), 400
            cur.execute("UPDATE users SET coins=%s, updated_at=NOW() WHERE telegram_id=%s", (after, uid))
            adj_id = "WAL-" + uuid.uuid4().hex[:12].upper()
            cur.execute("""INSERT INTO wallet_adjustments
                (id, telegram_id, admin_id, action, coins, balance_before, balance_after, reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (adj_id, uid, ADMIN_ID, action, amount, before, after, reason or None))
            conn.commit()
            return jsonify({"ok": True, "id": adj_id, "telegram_id": uid, "action": action, "coins": amount, "balance": after})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/admin/wallet/history")
@require_admin
def admin_wallet_history():
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT w.id,w.telegram_id,w.admin_id,w.action,w.coins,w.balance_before,w.balance_after,w.reason,w.created_at,
                               u.username,u.first_name,u.last_name
                        FROM wallet_adjustments w JOIN users u ON u.telegram_id=w.telegram_id
                        ORDER BY w.created_at DESC LIMIT 200""")
            rows = cur.fetchall()
            for r in rows:
                if r["created_at"]: r["created_at"] = r["created_at"].isoformat()
            return jsonify({"ok": True, "history": rows})
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
