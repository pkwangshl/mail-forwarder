import os
import re
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

USER       = os.environ["EMAIL_USER"]
PASS       = os.environ["EMAIL_PASS"]
FORWARD_TO = os.environ["FORWARD_TO"]
TARGET_SENDER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()

IMAP_ID = {
    "name":          "CloudForwarder",
    "version":       "1.4.1",
    "vendor":        "Railway",
    "support-email": USER,
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
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

# ---- 更宽松的 HTML 判定 ----
HTML_LIKE = re.compile(rb"(?i)\A\s*<(?:!doctype\s*html?|html|head|body)")

def looks_like_html(raw: bytes) -> bool:
    return bool(HTML_LIKE.match(raw))

def copy_parts(src: email.message.Message, dst: EmailMessage):
    text_done = html_done = False

    def handle_body(ctype, maintype, subtype, text, charset, raw):
        nonlocal text_done, html_done

        # 特判“伪 HTML”
        if ctype == "text/plain" and looks_like_html(raw) and not html_done:
            ctype, subtype, maintype = "text/html", "html", "text"

        # HTML正文
        if ctype == "text/html" and not html_done:
            if isinstance(raw, bytes) and raw:
                try:
                    s = raw.decode(charset, errors="replace")
                    dst.add_alternative(s, subtype="html", charset=charset)
                except Exception:
                    dst.add_alternative(raw, maintype="text", subtype="html", charset=charset)
            else:
                dst.add_alternative(text, subtype="html", charset=charset)
            html_done = True

        # 纯文本正文
        elif ctype == "text/plain" and not text_done:
            if isinstance(raw, bytes) and raw:
                try:
                    s = raw.decode(charset, errors="replace")
                    dst.set_content(s, subtype="plain", charset=charset)
                except Exception:
                    dst.set_content(raw, maintype="text", subtype="plain", charset=charset)
            else:
                dst.set_content(text, subtype="plain", charset=charset)
            text_done = True

        return ctype

    if src.is_multipart():
        for part in src.walk():
            if part.is_multipart():
                continue
            ctype     = part.get_content_type()
            maintype  = part.get_content_maintype()
            subtype   = part.get_content_subtype()
            filename  = part.get_filename()
            text, charset, raw = decode_payload(part)

            final_ctype = handle_body(ctype, maintype, subtype, text, charset, raw)

            if ((filename or maintype in {"image", "application", "audio", "video"})
                    and final_ctype == ctype and raw):
                dst.add_attachment(
                    raw,
                    maintype=maintype,
                    subtype=subtype,
                    filename=filename,
                    cid=part.get("Content-ID"),
                )
    else:
        ctype     = src.get_content_type()
        maintype  = src.get_content_maintype()
        subtype   = src.get_content_subtype()
        text, charset, raw = decode_payload(src)

        final_ctype = handle_body(ctype, maintype, subtype, text, charset, raw)

        if final_ctype == ctype and maintype not in {"text"}:
            dst.set_content("邮件内容为纯附件或图片。")
            dst.add_attachment(raw,
                               maintype=maintype,
                               subtype=subtype,
                               filename=src.get_filename())

    if not html_done and not text_done and not dst.get_content():
        dst.set_content("邮件内容为纯附件或图片。")

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
            subject = (orig.get("Subject", "") or "") \
                        .replace("\r", "").replace("\n", "")

            if sender != TARGET_SENDER:
                log.info("Skip mail from %s", sender)
                imap.add_flags(uid, [b"\\Seen"])
                continue

            log.info("Forwarding email from %s", sender)
            fwd = EmailMessage()
            fwd["Subject"] = subject
            fwd["From"]    = USER
            fwd["To"]      = FORWARD_TO
            fwd["Date"]    = email.utils.formatdate(localtime=True)

            try:
                copy_parts(orig, fwd)
            except Exception:
                log.exception("copy_parts() failed, fallback to .eml attachment.")
                fwd.set_content("原始邮件作为附件保留。")
                fwd.add_attachment(data[b"RFC822"],
                                   maintype="message",
                                   subtype="rfc822",
                                   filename="original.eml")

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