import os
import logging
from datetime import datetime
from flask import Flask
from imapclient import IMAPClient
import pytz
import requests

# 配置环境变量
IMAP_HOST = os.environ.get("IMAP_HOST")          # 比如 imap.qq.com
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
EMAIL_USER = os.environ.get("EMAIL_USER")        # QQ邮箱地址
EMAIL_PASS = os.environ.get("EMAIL_PASS")        # 授权码
TARGET_SENDER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_IDS = os.environ.get("ALLOWED_CHAT_IDS", "")  # 逗号分隔，多个

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mail-forwarder")

def in_japan_night() -> bool:
    jst = pytz.timezone("Asia/Tokyo")
    now = datetime.now(jst)
    return (now.hour == 23 and now.minute >= 50) or (0 <= now.hour < 6)

def send_to_telegram(subject, dt_str):
    chat_ids = [cid.strip() for cid in ALLOWED_CHAT_IDS.split(",") if cid.strip()]
    if not chat_ids:
        log.warning("No ALLOWED_CHAT_IDS set, skip telegram send.")
        return
    text = f"新邮件（{dt_str} JST）：\n{subject}"
    for chat_id in chat_ids:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text}
            )
            log.info(f"Sent to Telegram chat_id={chat_id}, resp={resp.status_code}")
        except Exception as e:
            log.error(f"Send to Telegram chat_id={chat_id} error: {e}")

def fetch_and_forward():
    if in_japan_night():
        log.info("夜间暂停窗口，跳过轮询。")
        return "Rest time"

    with IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True) as imap:
        imap.login(EMAIL_USER, EMAIL_PASS)
        imap.select_folder("INBOX")
        uids = imap.search(["UNSEEN"])
        if not uids:
            log.info("No new mail.")
            return "No new mail"

        log.info("Found %d new mails.", len(uids))
        for uid, data in imap.fetch(uids, ["RFC822", "ENVELOPE"]).items():
            envelope = data[b"ENVELOPE"]
            sender = envelope.from_[0].mailbox.decode() + "@" + envelope.from_[0].host.decode()
            subject = envelope.subject.decode(errors="ignore") if envelope.subject else ""
            if sender.lower() != TARGET_SENDER:
                log.info(f"Skip mail from {sender}")
                imap.add_flags(uid, [b"\\Seen"])
                continue

            dt = envelope.date.astimezone(pytz.timezone("Asia/Tokyo"))
            dt_str = dt.strftime("%Y-%m-%d %H:%M")
            send_to_telegram(subject, dt_str)
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
