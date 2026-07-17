import os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from email.header import Header
from datetime import datetime, timezone, timedelta

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PW = os.environ["GMAIL_APP_PW"]
TEST_BCC = "111715@koreainvestment.com"
KST = timezone(timedelta(hours=9))

BOT_NAME = "⚠️ eBiz 부정여론봇"
def _from_header():
    return formataddr((str(Header(BOT_NAME, "utf-8")), GMAIL_USER))
def _addr_header(addr):
    return formataddr((str(Header(BOT_NAME, "utf-8")), addr))

now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
msg = MIMEMultipart("alternative")
msg["Subject"] = f"[테스트] Bcc 수신 확인 | {now} KST"
msg["From"] = _from_header()
msg["To"] = _addr_header(GMAIL_USER)
msg.attach(MIMEText(
    f"<p>이 메일은 숨은참조(Bcc) 수신 테스트용입니다.</p>"
    f"<p>이 메일을 받으셨다면 111715@koreainvestment.com 계정으로 Bcc 방식 수신이 "
    f"정상 작동한다는 뜻입니다.</p><p>발송 시각: {now} KST</p>",
    "html", "utf-8"
))

all_recipients = [GMAIL_USER, TEST_BCC]
print(f"발송 시도: To={GMAIL_USER}, Bcc(숨김)={TEST_BCC}")
with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
    s.login(GMAIL_USER, GMAIL_APP_PW)
    refused = s.sendmail(GMAIL_USER, all_recipients, msg.as_string())

if refused:
    print(f"❌ 거부됨: {refused}")
else:
    print(f"✅ SMTP 서버가 전원 수락함 (거부 없음) - 실제 수신함 도착 여부는 별도 확인 필요")
