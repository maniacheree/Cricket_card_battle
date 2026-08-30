import os
from flask import Flask, send_from_directory, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)


@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "Cricket Card Arena"
    })


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
