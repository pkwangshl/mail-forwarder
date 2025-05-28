import os
from flask import Flask
from imapclient import IMAPClient
import email
from datetime import datetime
import pytz
import requests
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mail2telegram")

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.163.com")
EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASS = os.environ["EMAIL_PASS"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_IDS = [i.strip() for i in os.environ["ALLOWED_CHAT_IDS"].split(",") if i.strip()]
# 只关注这个发件人
TARGET_SENDER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()

IMAP_ID = {
    "name": "Mail2Telegram",
    "version": "1.0.0",
    "vendor": "Railway",
    "support-email": EMAIL_USER,
}

app = Flask(__name__)

def in_japan_night():
    jst = pytz.timezone("Asia/Tokyo")
    now = datetime.now(jst)
    return (now.hour == 23 and now.minute >= 50) or (0 <= now.hour < 6)

def send_telegram_message(title):
    jst = pytz.timezone("Asia/Tokyo")
    now_jst = datetime.now(jst).strftime("%Y-%m-%d %H:%M")
    msg = f"【MergerMarket新邮件】\n标题: {title}\n时间: {now_jst} (JST)"
    for chat_id in ALLOWED_CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": msg}
            )
        except Exception as e:
            log.error(f"推送到Telegram失败: {e}")

def fetch_and_notify():
    if in_japan_night():
        log.info("日本夜间暂停窗口，跳过。")
        return "Rest time"

    with IMAPClient(IMAP_HOST) as imap:
        imap.login(EMAIL_USER, EMAIL_PASS)
        imap.id_(IMAP_ID)
        imap.select_folder("INBOX")

        uids = imap.search(["UNSEEN"])
        if not uids:
            log.info("No new mail.")
            return "No new mail"

        log.info("Found %d new mails.", len(uids))
        for uid, data in imap.fetch(uids, ["RFC822"]).items():
            msg = email.message_from_bytes(data[b"RFC822"])
            sender = email.utils.parseaddr(msg.get("From"))[1].lower()
            subject = (msg.get("Subject", "") or "").replace("\r", "").replace("\n", "")

            if sender != TARGET_SENDER:
                imap.add_flags(uid, [b"\\Seen"])
                log.info(f"忽略其他发件人: {sender}")
                continue

            send_telegram_message(subject)
            imap.add_flags(uid, [b"\\Seen"])
            log.info(f"已转发: {subject}")

        return "Done"

@app.route("/trigger")
def trigger():
    try:
        result = fetch_and_notify()
        return f"Mail Check Result: {result}", 200
    except Exception as exc:
        log.exception("Error in /trigger")
        return f"Error: {exc}", 500

@app.route("/")
def home():
    return "Mail forward service running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
