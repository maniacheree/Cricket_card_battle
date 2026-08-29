from flask import Flask, jsonify
import os

app = Flask(__name__)

@app.get("/")
def home():
    return "Cricket Card Arena is ONLINE"

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "Cricket Card Arena"
    })

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080))
    )
