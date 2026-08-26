# server.py
# Python Flask backend skeleton for the Telegram Web App.
# IMPORTANT: set BOT_TOKEN as an environment variable. Never put it in index.html.

import os, hmac, hashlib, json, time
from urllib.parse import parse_qsl
from flask import Flask, request, jsonify

app=Flask(__name__)
BOT_TOKEN=os.environ["BOT_TOKEN"]

def verify_telegram_init_data(init_data: str):
    if not init_data:
        raise ValueError("Missing initData")
    data=dict(parse_qsl(init_data, keep_blank_values=True))
    received=data.pop("hash",None)
    if not received:
        raise ValueError("Missing hash")
    check="\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret=hmac.new(b"WebAppData",BOT_TOKEN.encode(),hashlib.sha256).digest()
    calculated=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated,received):
        raise ValueError("Invalid Telegram initData")
    # Optional freshness check.
    auth_date=int(data.get("auth_date","0"))
    if time.time()-auth_date > 86400:
        raise ValueError("Expired initData")
    return json.loads(data["user"])

@app.post("/api/auth/telegram")
def auth():
    try:
        user=verify_telegram_init_data(request.json.get("initData"))
        # TODO: load/create this Telegram ID in your database.
        return jsonify({"user":{"id":user["id"],"coins":0,"cards":0}})
    except Exception as e:
        return jsonify({"error":str(e)}),401

@app.post("/api/deposits")
def deposits():
    try:
        user=verify_telegram_init_data(request.json.get("initData"))
        payload=request.json
        # TODO: save amount, coins, UTR and screenshot to database/storage.
        # Do NOT credit coins here. Credit only after admin approval.
        return jsonify({"ok":True,"user_id":user["id"],"status":"PENDING"})
    except Exception as e:
        return jsonify({"error":str(e)}),401

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","8000")))
