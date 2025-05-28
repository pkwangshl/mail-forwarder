import os
import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
offset = 0
while True:
    resp = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}").json()
    for result in resp.get("result", []):
        message = result.get("message", {})
        chat = message.get("chat", {})
        print("chat_id:", chat.get("id"), "message:", message.get("text"))
        offset = result["update_id"] + 1
    if not resp.get("result"):
        break
