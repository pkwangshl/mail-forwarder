import os
from flask import Flask
from imapclient import IMAPClient
import email
from datetime import datetime
import pytz
import requests
import logging

IMAP_HOST = "imap.163.com"
USER = os.environ["EMAIL_USER"]
PASS = os.environ["EMAIL_PASS"]
TARGET_SENDER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()
ALLOWED_CHAT_IDS = os.environ.get("ALLOWED_CHAT_IDS", "").split(",")
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def in_japan_night():
    jst = pytz.timezone("Asia/Tokyo")
    now = datetime.now(jst)
    return (now.hour == 23 and now.minute >= 50) or (0 <= now.hour < 6)

def send_telegram(title):
    jst = pytz.timezone("Asia/Tokyo")
    now_str = datetime.now(jst).strftime('%Y-%m-%d %H:%M:%S')
    text = f"【MergerMarket新邮件】\n标题: {title}\n时间: {now_str}"
    for chat_id in ALLOWED_CHAT_IDS:
        chat_id = chat_id.strip()
        if chat_id:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": text}
                )
            except Exception as e:
                logging.error(f"Failed to send telegram to {chat_id}: {e}")

def fetch_and_forward():
    if in_japan_night():
        logging.info("夜间暂停窗口，跳过轮询。")
        return "Rest time"
    with IMAPClient(IMAP_HOST) as imap:
        imap.login(USER, PASS)
        imap.select_folder("INBOX")
        uids = imap.search(["UNSEEN"])
        if not uids:
            logging.info("No new mail.")
            return "No new mail"
        logging.info(f"Found {len(uids)} new mails.")
        for uid, data in imap.fetch(uids, ["RFC822"]).items():
            orig = email.message_from_bytes(data[b"RFC822"])
            sender = email.utils.parseaddr(orig.get("From"))[1].lower()
            subject = (orig.get("Subject", "") or "").replace("\r", "").replace("\n", "")
            if sender != TARGET_SENDER:
                logging.info(f"Skip mail from {sender}")
                imap.add_flags(uid, [b"\\Seen"])
                continue
            logging.info(f"Forwarding email from {sender}, title: {subject}")
            send_telegram(subject)
            imap.add_flags(uid, [b"\\Seen"])
        return "All mails processed"

@app.route("/trigger")
def trigger():
    try:
        res = fetch_and_forward()
        return f"Mail Check Result: {res}", 200
    except Exception as exc:
        logging.exception("Unhandled error in /trigger")
        return f"Error: {exc}", 500

@app.route("/")
def home():
    return "Mail forward service is running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
