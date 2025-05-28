import os
import logging
from datetime import datetime
import pytz
from flask import Flask
from imapclient import IMAPClient
import email
import requests

# 环境变量
IMAP_HOST = "imap.163.com"
USER = os.environ["EMAIL_USER"]
PASS = os.environ["EMAIL_PASS"]
TARGET_SENDER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_IDS = [int(i) for i in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if i.strip()]

IMAP_ID = {
    "name": "CloudForwarder",
    "version": "7.0.0",
    "vendor": "Railway",
    "support-email": USER,
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cloud-forwarder")

def in_japan_night() -> bool:
    jst = pytz.timezone("Asia/Tokyo")
    now = datetime.now(jst)
    return (now.hour == 23 and now.minute >= 50) or (0 <= now.hour < 6)

def fetch_and_notify():
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
        jst = pytz.timezone("Asia/Tokyo")
        now_jst = datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")

        for uid, data in imap.fetch(uids, ["RFC822"]).items():
            orig = email.message_from_bytes(data[b"RFC822"])
            sender = email.utils.parseaddr(orig.get("From"))[1].lower()
            subject = (orig.get("Subject", "") or "").replace("\r", "").replace("\n", "")

            if sender != TARGET_SENDER:
                log.info("Skip mail from %s", sender)
                imap.add_flags(uid, [b"\\Seen"])
                continue

            msg = f"【{now_jst} 东京时间】\n\n{subject}"
            for chat_id in ALLOWED_CHAT_IDS:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": msg}
                )
            log.info("Mail forwarded to Telegram.")

            imap.add_flags(uid, [b"\\Seen"])
        return "All mails processed"

@app.route("/trigger")
def trigger():
    try:
        res = fetch_and_notify()
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
