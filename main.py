import os
from datetime import datetime
from flask import Flask
from imapclient import IMAPClient
import email
import pytz
import requests
import logging

IMAP_HOST = "imap.163.com"
USER = os.environ["EMAIL_USER"]
PASS = os.environ["EMAIL_PASS"]

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_IDS = [int(x) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()]

SENDER_FILTER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()

IMAP_ID = {
    "name": "CloudForwarder",
    "version": "6.0.0",
    "vendor": "Railway",
    "support-email": USER,
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cloud-forwarder")

def in_japan_night():
    jst = pytz.timezone("Asia/Tokyo")
    now = datetime.now(jst)
    return (now.hour == 23 and now.minute >= 50) or (0 <= now.hour < 6)

def send_telegram(text):
    for chat_id in ALLOWED_CHAT_IDS:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text}
        )
        if not resp.ok:
            log.error(f"Failed to send to Telegram chat_id {chat_id}: {resp.text}")

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

        log.info(f"Found {len(uids)} new mails.")
        for uid, data in imap.fetch(uids, ["RFC822"]).items():
            msg = email.message_from_bytes(data[b"RFC822"])
            sender = email.utils.parseaddr(msg.get("From"))[1].lower()
            subject = (msg.get("Subject", "") or "").replace("\r", "").replace("\n", "")
            if sender != SENDER_FILTER:
                log.info(f"Skip mail from {sender}")
                imap.add_flags(uid, [b"\\Seen"])
                continue

            jst = pytz.timezone("Asia/Tokyo")
            time_str = datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")
            text = f"新邮件提醒：\n标题: {subject}\n时间: {time_str} (东京时间)"
            send_telegram(text)
            log.info(f"Telegram sent: {text}")
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

@app.route("/")
def home():
    return "Mail forward service is running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
