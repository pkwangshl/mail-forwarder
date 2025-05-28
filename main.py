import os
import logging
from datetime import datetime
from flask import Flask
from imapclient import IMAPClient
import smtplib
import email
from email.message import EmailMessage
import pytz

# ─── 基本参数 ──────────────────────────────────────────
IMAP_HOST = "imap.163.com"
SMTP_HOST = "smtp.163.com"
USER       = os.environ["EMAIL_USER"]      # 163 账户
PASS       = os.environ["EMAIL_PASS"]      # 163 授权码
FORWARD_TO = os.environ["FORWARD_TO"]      # 目标收件人，可多邮箱逗号隔开
TARGET_SENDER = os.environ.get("TARGET_SENDER", "info@mergermarket.com").lower()

IMAP_ID = {
    "name":    "CloudForwarder",
    "version": "1.1.0",
    "vendor":  "Railway",
    "support-email": USER,
}

# ─── Flask 应用 ─────────────────────────────────────────
app = Flask(__name__)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cloud-forwarder")

# ─── 工具函数 ──────────────────────────────────────────
def in_japan_night() -> bool:
    """23:50–06:00 之间暂停轮询，防止夜间骚扰转发（示范，可按需删掉）"""
    jst = pytz.timezone("Asia/Tokyo")
    now = datetime.now(jst)
    return (now.hour == 23 and now.minute >= 50) or (0 <= now.hour < 6)

def decode_payload(part):
    """返回：(文本/空字符串, charset, 原始 bytes)"""
    raw = part.get_payload(decode=True)
    charset = part.get_content_charset() or "utf-8"
    if raw is None:
        return "", charset, b""
    if isinstance(raw, bytes):
        try:
            return raw.decode(charset, errors="replace"), charset, raw
        except Exception:
            return "", charset, raw
    return str(raw), charset, raw.encode(charset, errors="replace")

def copy_parts(src: email.message.Message, dst: EmailMessage):
    """把 src 邮件正文与附件完整复制到 dst。"""
    text_done = html_done = False

    if src.is_multipart():
        for part in src.walk():
            if part.is_multipart():      # 跳过容器
                continue
            ctype     = part.get_content_type()
            maintype  = part.get_content_maintype()
            subtype   = part.get_content_subtype()
            filename  = part.get_filename()
            text, charset, raw = decode_payload(part)

            # ---- 正文：HTML --------------------------------------------------
            if ctype == "text/html" and not html_done:
                # str OK；bytes 需显式 maintype / subtype
                dst.add_alternative(text or raw,
                                    maintype=None if text else "text",
                                    subtype="html",
                                    charset=charset if text else None)
                html_done = True

            # ---- 正文：纯文本 -------------------------------------------------
            elif ctype == "text/plain" and not text_done:
                dst.set_content(text or raw,
                                maintype=None if text else "text",
                                subtype="plain",
                                charset=charset if text else None)
                text_done = True

            # ---- 附件 / 图片 -------------------------------------------------
            elif filename or maintype in {"image", "application", "audio", "video"}:
                if raw:
                    dst.add_attachment(raw,
                        maintype=maintype,
                        subtype=subtype,
                        filename=filename,
                        cid=part.get("Content-ID"))
    else:
        # 单一 part
        ctype     = src.get_content_type()
        maintype  = src.get_content_maintype()
        subtype   = src.get_content_subtype()
        text, charset, raw = decode_payload(src)

        if ctype == "text/html":
            dst.add_alternative(text or raw,
                                maintype=None if text else "text",
                                subtype="html",
                                charset=charset if text else None)
        elif ctype == "text/plain":
            dst.set_content(text or raw,
                            maintype=None if text else "text",
                            subtype="plain",
                            charset=charset if text else None)
        else:
            dst.set_content("邮件内容为纯附件或图片。")
            dst.add_attachment(raw, maintype=maintype, subtype=subtype,
                               filename=src.get_filename())

    if not html_done and not text_done and not dst.get_content():
        dst.set_content("邮件内容为纯附件或图片。")

# ─── 主流程 ──────────────────────────────────────────
def fetch_and_forward():
    if in_japan_night():
        log.info("日本夜间休眠窗口，跳过轮询。")
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

            log.info("Forwarding email from %s", sender)
            fwd = EmailMessage()
            fwd["Subject"] = subject
            fwd["From"]    = USER
            fwd["To"]      = FORWARD_TO
            fwd["Date"]    = email.utils.formatdate()

            try:
                copy_parts(orig, fwd)
            except Exception:
                # 万一解析失败，直接把原始邮件作为 .eml 附件转发
                log.exception("copy_parts() failed, falling back to .eml attachment.")
                fwd.set_content("原始邮件已作为附件保留。")
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

# ─── Flask 路由 ──────────────────────────────────────
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

# ─── 入口 ────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)