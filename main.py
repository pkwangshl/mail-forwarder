import os
from flask import Flask, request
from imapclient import IMAPClient
import email
from email.header import decode_header
from datetime import datetime
import pytz
from telegram import Bot

# 环境变量
IMAP_HOST = "imap.163.com"
USER = os.environ["EMAIL_USER"]
PASS = os.environ["EMAIL_PASS"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_IDS = os.environ["ALLOWED_CHAT_IDS"].split(",")  # 多个chat_id用逗号分隔
TARGET_SENDER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()

# Flask App
app = Flask(__name__)
bot = Bot(token=TELEGRAM_BOT_TOKEN)

def in_japan_night() -> bool:
    jst = pytz.timezone("Asia/Tokyo")
    now = datetime.now(jst)
    return (now.hour == 23 and now.minute >= 50) or (0 <= now.hour < 6)

def get_subject(msg):
    subject = msg.get("Subject", "")
    dh = decode_header(subject)
    s, enc = dh[0]
    if isinstance(s, bytes):
        try:
            return s.decode(enc if enc else "utf-8")
        except:
            return s.decode("utf-8", errors="ignore")
    return s

def fetch_and_notify():
    if in_japan_night():
        return "Rest time"
    with IMAPClient(IMAP_HOST) as imap:
        imap.login(USER, PASS)
        imap.select_folder("INBOX")
        uids = imap.search(["UNSEEN"])
        if not uids:
            return "No new mail"
        for uid, data in imap.fetch(uids, ["RFC822"]).items():
            orig = email.message_from_bytes(data[b"RFC822"])
            sender = email.utils.parseaddr(orig.get("From"))[1].lower()
            if sender != TARGET_SENDER:
                imap.add_flags(uid, [b"\\Seen"])
                continue
            subject = get_subject(orig)
            text = None
            html = None
            for part in orig.walk():
                if part.get_content_type() == "text/plain" and not text:
                    text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                elif part.get_content_type() == "text/html" and not html:
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
            message = f"【MergerMarket新邮件】\n标题: {subject}\n"
            if text:
                message += f"\n{text[:1500]}"
            elif html:
                # 只保留纯文本
                import re
                text_from_html = re.sub("<[^<]+?>", "", html)
                message += f"\n{text_from_html[:1500]}"
            else:
                message += "(无正文内容)"
            # 发送到所有白名单 chat_id
            for cid in ALLOWED_CHAT_IDS:
                bot.send_message(chat_id=cid.strip(), text=message)
            imap.add_flags(uid, [b"\\Seen"])
        return "All mails processed"

@app.route("/trigger")
def trigger():
    try:
        res = fetch_and_notify()
        return f"Mail Check Result: {res}", 200
    except Exception as e:
        return f"Error: {e}", 500

@app.route("/")
def home():
    return "Mail forward service (Telegram mode) running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
