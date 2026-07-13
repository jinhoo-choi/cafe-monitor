"""
test_no_login_v2.py
- monitor.py의 실제 get_post_detail() 로직을 그대로 import해서 사용
- 같은 게시글을 [비로그인 컨텍스트]와 [로그인 컨텍스트]로 각각 열람 비교
- → "로그인 필요" vs "타이밍/셀렉터 문제"를 카페별로 확정 판별
"""
import os, smtplib, urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

# monitor.py import를 위한 필수 env (실제 값은 workflow secret으로 주입됨)
os.environ.setdefault("CLAUDE_API_KEY", "dummy-for-import")
os.environ.setdefault("NOTIFY_EMAIL", "dummy@dummy.com")

from playwright.sync_api import sync_playwright
import monitor as m  # 실제 프로덕션 모듈

KST = timezone(timedelta(hours=9))
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_APP_PW = os.environ["GMAIL_APP_PW"]
COOKIE_JSON  = os.environ.get("NAVER_COOKIES_JSON", "")

CAFES = m.CAFES  # 실제 14개 목록 그대로

logs = []
def log(msg):
    print(msg)
    logs.append(msg)

# monitor.log를 우리 로그로 우회 (본문수집 성공 메시지 캡처)
m.log = log

def send_result_email(summary_line):
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[비로그인 재검증 v2] {summary_line} | {now_str} KST"
    msg["From"] = f"검증봇 <{GMAIL_USER}>"
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(f"<pre>{chr(10).join(logs)}</pre>", "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())

def main():
    results = []
    # 쿠키 파일 준비 (로그인 컨텍스트용)
    has_cookie = bool(COOKIE_JSON.strip())
    if has_cookie:
        with open("naver_cookies.json", "w") as f:
            f.write(COOKIE_JSON)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx_anon  = browser.new_context()  # 비로그인
            page_anon = ctx_anon.new_page()
            if has_cookie:
                ctx_auth  = browser.new_context(storage_state="naver_cookies.json")
                page_auth = ctx_auth.new_page()

            for cafe in CAFES:
                cafe_id, num_id, name = cafe["id"], cafe["num_id"], cafe["name"]
                log(f"\n{'='*55}")
                log(f"[{name}] ({cafe_id})")

                row = {"name": name, "anon": None, "auth": None}

                # 비로그인으로 검색해서 첫 게시글 URL 확보
                q = urllib.parse.quote("한투")
                search_url = f"https://cafe.naver.com/f-e/cafes/{num_id}/menus/0?viewType=L&ta=ARTICLE_COMMENT&page=1&q={q}"
                post_url = None
                try:
                    page_anon.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                    page_anon.wait_for_timeout(2500)
                    rows = page_anon.query_selector_all("a.article")
                    if rows:
                        href = rows[0].get_attribute("href") or ""
                        if href.startswith("/"):
                            post_url = "https://cafe.naver.com" + href
                        elif href.startswith("http"):
                            post_url = href
                    log(f"  검색: {len(rows)}건 → 대상: {post_url[:70] if post_url else '없음'}")
                except Exception as e:
                    log(f"  검색 오류: {e}")

                if not post_url:
                    log("  → 검색 결과 없음, 건너뜀")
                    results.append(row)
                    continue

                # A: 비로그인으로 실제 get_post_detail() 실행
                try:
                    body_a, _ = m.get_post_detail(page_anon, post_url, cafe_id)
                    row["anon"] = len(body_a)
                    log(f"  [비로그인] 본문: {len(body_a)}자")
                except Exception as e:
                    log(f"  [비로그인] 오류: {e}")
                    row["anon"] = -1

                # B: 로그인 쿠키로 동일 게시글 실행
                if has_cookie:
                    try:
                        body_b, _ = m.get_post_detail(page_auth, post_url, cafe_id)
                        row["auth"] = len(body_b)
                        log(f"  [로그인]   본문: {len(body_b)}자")
                    except Exception as e:
                        log(f"  [로그인]   오류: {e}")
                        row["auth"] = -1

                results.append(row)

            browser.close()
    except Exception as e:
        import traceback
        log(f"\n❌ 전체 오류: {traceback.format_exc()}")

    # 요약
    log(f"\n\n{'='*55}")
    log("판정 요약  (비로그인 / 로그인)")
    log(f"{'='*55}")
    anon_ok = 0
    need_login = []
    for r in results:
        a, b = r["anon"], r["auth"]
        if a is None:
            verdict = "검색결과 없음"
        elif a and a > 0:
            verdict = "✅ 비로그인 가능"
            anon_ok += 1
        elif (a == 0 or a == -1) and b and b > 0:
            verdict = "🔴 로그인 필요"
            need_login.append(r["name"])
        elif (a == 0 or a == -1) and (b == 0 or b == -1):
            verdict = "⚠️ 양쪽 다 실패 (셀렉터/타이밍 문제)"
        else:
            verdict = "❓ 판정불가"
        log(f"  {str(a):>6} / {str(b):>6} | {verdict} | {r['name']}")

    tested = len([r for r in results if r["anon"] is not None])
    log(f"\n비로그인 성공: {anon_ok}/{tested}")
    log(f"로그인 필요 카페: {need_login if need_login else '없음'}")

    send_result_email(f"비로그인 {anon_ok}/{tested} · 로그인필요 {len(need_login)}곳")

if __name__ == "__main__":
    main()
