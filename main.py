import os
from flask import Flask
from imapclient import IMAPClient
import smtplib
from email.message import EmailMessage
import email
from datetime import datetime
import pytz

app = Flask(__name__)

IMAP_HOST = 'imap.163.com'
SMTP_HOST = 'smtp.163.com'
USER = os.environ['EMAIL_USER']
PASS = os.environ['EMAIL_PASS']
FORWARD_TO = os.environ['FORWARD_TO']

IMAP_ID = {
    "name": "RailwayScript",
    "version": "1.0.0",
    "vendor": "Railway",
    "support-email": USER
}

TARGET_SENDER = "info@mergermarket.com"

def is_japan_rest_time():
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)
    # 23:50~5:59 休息
    if (now_jst.hour == 23 and now_jst.minute >= 50) or (0 <= now_jst.hour < 6):
        return True
    return False

def fetch_and_forward():
    if is_japan_rest_time():
        print("Now is Japan rest time. Skipping task.")
        return "Rest time"
    with IMAPClient(IMAP_HOST) as server:
        server.login(USER, PASS)
        server.id_(IMAP_ID)
        server.select_folder('INBOX')
        messages = server.search(['UNSEEN'])
        if not messages:
            print("No new mail.")
            return "No new mail"
        print(f"Found {len(messages)} new mails.")
        for uid, msg in server.fetch(messages, ['RFC822']).items():
            msg_obj = email.message_from_bytes(msg[b'RFC822'])
            sender = email.utils.parseaddr(msg_obj.get('From'))[1]
            subject = msg_obj.get('Subject', '').replace('\n', '').replace('\r', '')
            if sender.lower() == TARGET_SENDER:
                print(f"Forwarding email from {sender}")
                email_message = EmailMessage()
                email_message['Subject'] = subject
                email_message['From'] = USER
                email_message['To'] = FORWARD_TO
                # 正确处理 multipart/单 part、HTML、文本和附件
                if msg_obj.is_multipart():
                    has_html = False
                    for part in msg_obj.walk():
                        if part.get_content_maintype() == 'multipart':
                            continue
                        content_type = part.get_content_type()
                        payload = part.get_payload(decode=True)
                        filename = part.get_filename()
                        charset = part.get_content_charset() or 'utf-8'
                        if content_type == 'text/html':
                            html = payload.decode(charset, errors='replace')
                            email_message.add_alternative(html, subtype='html')
                            has_html = True
                        elif content_type == 'text/plain' and not has_html:
                            text = payload.decode(charset, errors='replace')
                            email_message.set_content(text)
                        elif filename:
                            email_message.add_attachment(payload,
                                maintype=part.get_content_maintype(),
                                subtype=part.get_content_subtype(),
                                filename=filename)
                else:
                    content_type = msg_obj.get_content_type()
                    payload = msg_obj.get_payload(decode=True)
                    charset = msg_obj.get_content_charset() or 'utf-8'
                    if content_type == 'text/html':
                        html = payload.decode(charset, errors='replace')
                        email_message.add_alternative(html, subtype='html')
                    else:
                        text = payload.decode(charset, errors='replace')
                        email_message.set_content(text)
                with smtplib.SMTP_SSL(SMTP_HOST, 465) as smtp:
                    smtp.login(USER, PASS)
                    smtp.send_message(email_message)
                print("Mail forwarded.")
            else:
                print(f"Skipped mail from {sender}")
            server.add_flags(uid, [b'\\Seen'])
        return "All mails forwarded"

@app.route("/trigger")
def trigger():
    result = fetch_and_forward()
    return f"Mail Check Result: {result}", 200

@app.route("/")
def home():
    return "Mail forward service running!", 200

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=port)
