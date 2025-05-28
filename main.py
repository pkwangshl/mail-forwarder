import os
import logging
from flask import Flask, request
from imapclient import IMAPClient
import email
import pytz
from datetime import datetime
import requests

IMAP_HOST = "imap.163.com"
USER = os.environ["EMAIL_USER"]
PASS = os.environ["EMAIL_PASS"]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_IDS = set(str(cid).strip() for cid in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if cid.strip())
TARGET_SENDER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()

IMAP_ID = {
    "name": "CloudForwarder",
    "version": "7.0.0",
    "vendor": "Railway",
    "support-email": USER,
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mail2telegram")

def in_japan_night() -> bool:
    jst = pytz.timezone("Asia/Tokyo")
    now = datetime.now(jst)
    return (now.hour == 23 and now.minute >= 50) or (0 <= now.hour < 6)

def get_telegram_api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

def send_telegram_text(chat_id, text):
    url = get_telegram_api_url("sendMessage")
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    r = requests.post(url, data=data)
    return r.json()

def send_telegram_photo(chat_id, photo_bytes, caption=None):
    url = get_telegram_api_url("sendPhoto")
    files = {'photo': ('image.jpg', photo_bytes)}
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"
    r = requests.post(url, files=files, data=data)
    return r.json()

def fetch_and_forward():
    if in_japan_night():
        log.info("夜间暂停窗口，跳过轮询。")
        return "Rest time"

    with IMAPClient(IMAP_HOST) as imap:
        imap.login(USER, PASS)
        imap.id_(IMAP_ID)
        imap.select_folder("INBOX")
        uids = imap.search(["UNSEEN"])
        if not uids:
            log.info("No new mail.")
            return "No new mail"
        log.info("Found %d new mails.", len(uids))
        for uid, data in imap.fetch(uids, ["RFC822"]).items():
            orig = email.message_from_bytes(data[b"RFC822"])
            sender  = email.utils.parseaddr(orig.get("From"))[1].lower()
            subject = (orig.get("Subject", "") or "").replace("\r", "").replace("\n", "")

            if sender != TARGET_SENDER:
                log.info("Skip mail from %s", sender)
                imap.add_flags(uid, [b"\\Seen"])
                continue

            # 合成内容
            html_body = ""
            text_body = ""
            images = []
            if orig.is_multipart():
                for part in orig.walk():
                    ctype = part.get_content_type()
                    if ctype == "text/plain" and not text_body:
                        charset = part.get_content_charset() or "utf-8"
                        text_body = part.get_payload(decode=True).decode(charset, errors="replace")
                    elif ctype == "text/html" and not html_body:
                        charset = part.get_content_charset() or "utf-8"
                        html_body = part.get_payload(decode=True).decode(charset, errors="replace")
                    elif ctype.startswith("image/"):
                        images.append((part.get_filename(), part.get_payload(decode=True)))
            else:
                ctype = orig.get_content_type()
                if ctype == "text/plain":
                    charset = orig.get_content_charset() or "utf-8"
                    text_body = orig.get_payload(decode=True).decode(charset, errors="replace")
                elif ctype == "text/html":
                    charset = orig.get_content_charset() or "utf-8"
                    html_body = orig.get_payload(decode=True).decode(charset, errors="replace")

            # 取合适的正文
            body = html_body if html_body else text_body if text_body else "(无正文内容)"
            if len(body) > 4000:
                body = body[:3900] + "\n...(正文过长已截断)"

            msg_head = f"【<b>{subject}</b>】\n"
            msg_head += f"来自: <code>{sender}</code>\n\n"

            for chat_id in ALLOWED_CHAT_IDS:
                # 发正文
                send_telegram_text(chat_id, msg_head + body)
                # 发图片
                for filename, img_bytes in images:
                    send_telegram_photo(chat_id, img_bytes, caption=f"<b>{subject}</b>" if subject else None)

            imap.add_flags(uid, [b"\\Seen"])
        return "All mails processed"

@app.route("/trigger")
def trigger():
    try:
        res = fetch_and_forward()
        return f"Mail Check Result: {res}", 200
    except Exception as exc:
        log.exception("Unhandled error in /trigger")
        return f"Error: {exc}", 500

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data:
        return "No data", 400
    msg = data.get("message", {})
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = msg.get("text", "")
    user = msg.get("from", {}).get("username", "")
    # 允许任意人 /start，主动告诉他chat_id
    if text and text.strip().startswith("/start"):
        send_telegram_text(chat_id, f"你好，你的chat_id是 <code>{chat_id}</code>。\n请告知管理员加白名单后即可收到推送。")
        log.info(f"User @{user} started bot, chat_id={chat_id}")
    return "ok", 200

@app.route("/")
def home():
    return "Mail2Telegram bot is running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
