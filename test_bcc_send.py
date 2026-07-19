"""실운영 알림 메일과 동일한 조건(개별발송 + 대용량 HTML + 외부 링크)으로
111715 계정 수신 여부를 검증하는 일회성 테스트."""
import os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid
from email.header import Header
from datetime import datetime, timezone, timedelta

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PW = os.environ["GMAIL_APP_PW"]
TEST_TARGET = "111715@koreainvestment.com"
KST = timezone(timedelta(hours=9))

BOT_NAME = "⚠️ eBiz 부정여론봇"
def _from_header():
    return formataddr((str(Header(BOT_NAME, "utf-8")), GMAIL_USER))
def _addr_header(addr):
    return formataddr((str(Header(BOT_NAME, "utf-8")), addr))

now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

# 실제 알림 메일과 유사한 구조: 빨간 배너 + 카드 + 네이버 카페 외부 링크 다수
card = """
<table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid #eee;border-radius:8px;margin:12px 0;">
  <tr><td style="padding:16px;">
    <div style="font-size:12px;color:#c0392b;">💬 카페 · 게시글 {i}/3 · 테스트카페 · 탐지 키워드: 한투 · 부정 강도: 3/10</div>
    <div style="font-size:16px;font-weight:bold;margin:8px 0;">[개별발송 테스트] 샘플 게시글 제목 {i}</div>
    <div style="font-size:13px;color:#555;">이 카드는 실제 알림 메일과 동일한 레이아웃/링크 구조를 재현한 테스트입니다.</div>
    <a href="https://cafe.naver.com/f-e/cafes/10322296/articles/123456{i}" style="color:#2980b9;">게시글 바로가기</a>
  </td></tr>
</table>"""
cards = "".join(card.replace("{i}", str(i)) for i in range(1, 4))

html_body = f"""<!DOCTYPE html>
<html><body style="margin:0;background:#f4f4f4;font-family:'Malgun Gothic',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:20px;">
<table width="600" cellpadding="0" cellspacing="0">
  <tr><td style="background:#b71c1c;color:#fff;padding:20px;border-radius:8px 8px 0 0;">
    <div style="font-size:12px;">AI 부정여론 탐지 · 테스트</div>
    <div style="font-size:20px;font-weight:bold;">[테스트] 개별발송 방식 수신 확인</div>
    <div style="font-size:12px;margin-top:6px;">{now} KST · 크롤링 29건 ▶ 키워드 탐지 29건 ▶ AI 필터링 3건</div>
  </td></tr>
  <tr><td style="background:#fafafa;padding:16px;border-radius:0 0 8px 8px;">
    {cards}
    <div style="font-size:12px;color:#999;margin-top:12px;">
      이 메일을 받으셨다면 개별발송 방식(To=수신자 본인)이 실운영 콘텐츠 조건에서도
      정상 수신됨이 확인된 것입니다. 발송 시각: {now} KST
    </div>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

# 실운영과 동일: 수신자별 개별 발송 (To = 각자 자기 주소)
targets = [GMAIL_USER, TEST_TARGET]
refused_all = {}
with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
    s.login(GMAIL_USER, GMAIL_APP_PW)
    for addr in targets:
        m = MIMEMultipart("alternative")
        m["Subject"] = f"[테스트] 개별발송 실운영 조건 수신 확인 | {now} KST"
        m["From"] = _from_header()
        m["To"] = _addr_header(addr)
        m["Date"] = formatdate(localtime=True)
        m["Message-ID"] = make_msgid(domain="gmail.com")
        m.attach(MIMEText(html_body, "html", "utf-8"))
        refused = s.sendmail(GMAIL_USER, [addr], m.as_string())
        print(f"→ {addr}: {'❌ 거부 ' + str(refused) if refused else '✅ SMTP 수락'}")
        if refused:
            refused_all.update(refused)

print("완료 - 거부:", refused_all if refused_all else "없음")
