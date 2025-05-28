import os
import logging
from datetime import datetime
from flask import Flask, request
from imapclient import IMAPClient
import smtplib
import email
from email.message import EmailMessage
import pytz
import requests

IMAP_HOST = "imap.163.com"
SMTP_HOST = "smtp.163.com"

USER = os.environ["EMAIL_USER"]
PASS = os.environ["EMAIL_PASS"]
# 多个 chat_id 用英文逗号分隔
ALLOWED_CHAT_IDS = [cid.strip() for cid in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if cid.strip()]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")  # 你的 chat_id，收新用户提醒
TARGET_SENDER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()

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

def decode_payload(part):
    raw = part.get_payload(decode=True)
    charset = part.get_content_charset() or "utf-8"
    if raw is None:
        return "", charset, b""
    if isinstance(raw, bytes):
        try:
            text = raw.decode(charset, errors="replace")
        except Exception:
            text = ""
        return text, charset, raw
    txt = str(raw)
    return txt, charset, txt.encode(charset, errors="replace")

def send_telegram(chat_id, text=None, photo=None, caption=None):
    url_base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if photo:
        files = {'photo': photo}
        data = {'chat_id': chat_id, 'caption': caption or ''}
        requests.post(f"{url_base}/sendPhoto", data=data, files=files)
    elif text:
        requests.post(f"{url_base}/sendMessage", json={'chat_id': chat_id, 'text': text})

def notify_admin_new_user(chat_id, username, first_name):
    if not ADMIN_CHAT_ID:
        return
    msg = f"新用户: {first_name or ''} (@{username})\nchat_id: {chat_id}"
    send_telegram(ADMIN_CHAT_ID, text=msg)

def copy_parts_and_send_telegram(orig):
    text_content = None
    html_content = None
    images = []
    attachments = []

    if orig.is_multipart():
        for part in orig.walk():
            ctype = part.get_content_type()
            maintype = part.get_content_maintype()
            filename = part.get_filename()
            payload = part.get_payload(decode=True)
            if ctype == "text/plain" and not text_content:
                charset = part.get_content_charset() or "utf-8"
                text_content = payload.decode(charset, errors="replace") if payload else ""
            elif ctype == "text/html" and not html_content:
                charset = part.get_content_charset() or "utf-8"
                html_content = payload.decode(charset, errors="replace") if payload else ""
            elif maintype == "image":
                images.append((filename, payload))
            elif maintype in {"application", "audio", "video"} or filename:
                attachments.append((filename, payload))
    else:
        ctype = orig.get_content_type()
        maintype = orig.get_content_maintype()
        filename = orig.get_filename()
        payload = orig.get_payload(decode=True)
        if ctype == "text/plain":
            charset = orig.get_content_charset() or "utf-8"
            text_content = payload.decode(charset, errors="replace") if payload else ""
        elif ctype == "text/html":
            charset = orig.get_content_charset() or "utf-8"
            html_content = payload.decode(charset, errors="replace") if payload else ""
        elif maintype == "image":
            images.append((filename, payload))

    message = ""
    if html_content:
        # Telegram 不直接支持 HTML 邮件，转成纯文本
        from bs4 import BeautifulSoup
        message = BeautifulSoup(html_content, "html.parser").get_text()
    elif text_content:
        message = text_content
    else:
        message = "(此邮件无正文，可能只有图片或附件)"

    subject = (orig.get("Subject", "") or "").replace("\r", "").replace("\n", "")
    full_msg = f"【{subject}】\n{message}"

    # 发文字
    for chat_id in ALLOWED_CHAT_IDS:
        send_telegram(chat_id, text=full_msg[:4096])
        # 发图片
        for fname, img_data in images[:2]:  # 最多发2张，防止风控
            send_telegram(chat_id, photo=img_data, caption=subject[:1024])
        # 可加附件代码，但 Telegram 普通 bot 不能直接发非图片附件

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
            sender = email.utils.parseaddr(orig.get("From"))[1].lower()
            if sender != TARGET_SENDER:
                log.info("Skip mail from %s", sender)
                imap.add_flags(uid, [b"\\Seen"])
                continue
            log.info("Forwarding email from %s", sender)
            try:
                copy_parts_and_send_telegram(orig)
            except Exception as e:
                log.exception("telegram发送失败: %s", e)
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

# webhook 用于自动收集新用户 chat_id
@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    data = request.get_json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    username = message.get("from", {}).get("username", "")
    first_name = message.get("from", {}).get("first_name", "")
    if chat_id:
        notify_admin_new_user(chat_id, username, first_name)
    return "ok", 200

@app.route("/")
def home():
    return "Mail forward service is running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
