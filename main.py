import os
import logging
from datetime import datetime
from flask import Flask
from imapclient import IMAPClient
import smtplib
import email
from email.message import EmailMessage
import pytz

IMAP_HOST = "imap.163.com"
SMTP_HOST = "smtp.163.com"

USER = os.environ["EMAIL_USER"]
PASS = os.environ["EMAIL_PASS"]
FORWARD_TO = os.environ["FORWARD_TO"]
TARGET_SENDER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()

IMAP_ID = {
    "name": "CloudForwarder",
    "version": "2.0.0",
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
            subject = (orig.get("Subject", "") or "").replace("\r", "").replace("\n", "")

            if sender != TARGET_SENDER:
                log.info("Skip mail from %s", sender)
                imap.add_flags(uid, [b"\\Seen"])
                continue

            log.info("Forwarding email from %s", sender)
            fwd = EmailMessage()
            fwd["Subject"] = subject + " [原始邮件自动转发]"
            fwd["From"] = USER
            fwd["To"] = FORWARD_TO
            fwd["Date"] = email.utils.formatdate(localtime=True)
            fwd.set_content("原始163邮件已作为 .eml 附件保留，请用Gmail等邮件客户端打开查看完整格式。")

            # 直接把原始邮件作为附件
            fwd.add_attachment(
                data[b"RFC822"],
                maintype="message",
                subtype="rfc822",
                filename="original.eml"
            )

            with smtplib.SMTP_SSL(SMTP_HOST, 465) as smtp:
                smtp.login(USER, PASS)
                smtp.send_message(fwd)
            log.info("Mail forwarded.")

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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)