import os
from flask import Flask, request
import requests

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
app = Flask(__name__)

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json()
    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "")
    # 回复消息，显示 chat_id
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": f"你的 chat_id: {chat_id}\n你发的内容：{text}"}
    )
    return "ok", 200

@app.route("/")
def home():
    return "ok", 200

if __name__ == "__main__":
    app.run(port=8080)
