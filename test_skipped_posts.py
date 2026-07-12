"""
test_skipped_posts.py
- '키워드 미확인' 스킵된 블로그 글 4건의 실제 본문 확인
- 키워드가 정말 없는지 vs 2000자 잘림 때문인지 판별
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_APP_PW = os.environ["GMAIL_APP_PW"]

KEYWORDS = ["한국투자증권", "한투", "뱅키스"]

# 오늘 로그에서 '키워드 미확인' 스킵된 글 중 4건
TEST_URLS = [
    "https://m.blog.naver.com/okykr/224344418041",
    "https://m.blog.naver.com/sheherazade2/224344422838",
    "https://m.blog.naver.com/khunter815/224344412272",
    "https://m.blog.naver.com/jeicox/224344313366",
]

logs = []
def log(msg):
    print(msg)
    logs.append(msg)

def send_result_email():
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[스킵글 검증] 키워드 위치 확인 | {now_str} KST"
    msg["From"] = f"검증봇 <{GMAIL_USER}>"
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(f"<pre>{chr(10).join(logs)}</pre>", "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())

def main():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
            )
            for i, url in enumerate(TEST_URLS, 1):
                log(f"\n{'='*55}")
                log(f"[{i}/{len(TEST_URLS)}] {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(2000)
                except Exception as e:
                    log(f"  로드 실패: {e}")
                    continue

                # 제목
                title = ""
                for sel in [".se-title-text", ".htitle", "h3.se_textarea"]:
                    el = page.query_selector(sel)
                    if el:
                        title = el.inner_text().strip()
                        if title:
                            break
                log(f"제목: {title[:70]}")

                # 본문 전체 (자르지 않음)
                body_full = ""
                for sel in [".se-main-container", "#postViewArea", ".post_ct"]:
                    el = page.query_selector(sel)
                    if el:
                        body_full = el.inner_text().strip()
                        if body_full:
                            break

                log(f"본문 전체 길이: {len(body_full)}자")
                body_2000 = body_full[:2000]

                check_full = title + " " + body_full
                check_2000 = title + " " + body_2000

                for kw in KEYWORDS:
                    in_full = kw in check_full
                    in_2000 = kw in check_2000
                    pos = check_full.find(kw) if in_full else -1
                    status = ""
                    if in_full and not in_2000:
                        status = f"🔴 2000자 잘림 피해! (위치: {pos}자)"
                    elif in_full:
                        status = f"✅ 앞부분에 있음 (위치: {pos}자)"
                    else:
                        status = "정말 없음"
                    log(f"  '{kw}': 전체={in_full}, 2000자내={in_2000} → {status}")

            browser.close()
        send_result_email()
    except Exception as e:
        import traceback
        log(f"오류: {traceback.format_exc()}")
        send_result_email()
        raise

if __name__ == "__main__":
    main()
