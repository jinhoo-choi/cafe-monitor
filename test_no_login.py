"""
test_no_login.py
- 네이버 카페 검색/본문 열람이 로그인 없이 가능한지 검증
- 쿠키 파일을 아예 사용하지 않는 fresh context로 테스트
"""
import smtplib, os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_APP_PW = os.environ["GMAIL_APP_PW"]

logs = []
def log(msg):
    print(msg)
    logs.append(msg)

def send_result_email():
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[비로그인 검증] 카페 접근 가능 여부 | {now_str} KST"
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
            # 쿠키 전혀 없는 완전 새 컨텍스트 (로그인 안 함)
            context = browser.new_context()
            page = context.new_page()

            # ── 테스트 1: 카페 검색 (로그인 없이) ──
            log("="*55)
            log("테스트 1: 검색 결과 페이지 접근")
            log("="*55)
            search_url = "https://cafe.naver.com/f-e/cafes/28497937/menus/0?viewType=L&ta=ARTICLE_COMMENT&page=1&q=%ED%95%9C%ED%88%AC"
            page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
            log(f"최종 URL: {page.url}")
            log(f"로그인 페이지로 리디렉션됨: {'nid.naver.com' in page.url}")

            rows = page.query_selector_all("a.article")
            log(f"검색 결과 게시글 링크 수: {len(rows)}개")
            if rows:
                log(f"첫 게시글 제목: {rows[0].inner_text().strip()[:50]}")

            # ── 테스트 2: 실제 게시글 본문 열람 (로그인 없이) ──
            log("\n" + "="*55)
            log("테스트 2: 게시글 본문 열람")
            log("="*55)
            # 최근 실제 존재 확인된 게시글 URL 재사용
            post_url = "https://cafe.naver.com/f-e/cafes/28497937/articles/1481428"
            page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
            SELECTOR_COMBINED = ".se-main-container, .ArticleContentBox, #tbody"
            try:
                page.wait_for_selector(SELECTOR_COMBINED, timeout=8000)
            except Exception:
                page.wait_for_timeout(3000)

            log(f"최종 URL: {page.url}")
            log(f"로그인 페이지로 리디렉션됨: {'nid.naver.com' in page.url}")

            body_text = ""
            for frame in page.frames:
                frame_url = frame.url or ""
                if "naver.com" in frame_url and frame_url != page.url and frame_url != "about:blank":
                    for sel in [".se-main-container", ".ArticleContentBox", "#tbody"]:
                        el = frame.query_selector(sel)
                        if el:
                            t = el.inner_text().strip()
                            if t:
                                body_text = t
                                break
                    if body_text:
                        break
            if not body_text:
                for sel in [".se-main-container", ".ArticleContentBox", "#tbody"]:
                    el = page.query_selector(sel)
                    if el:
                        body_text = el.inner_text().strip()
                        if body_text:
                            break

            log(f"본문 수집 성공: {bool(body_text)} ({len(body_text)}자)")
            if body_text:
                log(f"본문 미리보기: {body_text[:150]}")
            else:
                # 로그인 유도 문구 확인
                page_text = page.inner_text("body") if page.query_selector("body") else ""
                if "로그인" in page_text or "가입" in page_text:
                    log("⚠️ 페이지에 '로그인' 또는 '가입' 관련 문구 발견 - 접근 제한 추정")
                    log(f"페이지 텍스트 일부: {page_text[:300]}")

            # ── 테스트 3: 다른 카페(평생주식카페 - 회원수 많은 대형카페)도 확인 ──
            log("\n" + "="*55)
            log("테스트 3: 다른 카페(평생주식카페) 검색")
            log("="*55)
            search_url2 = "https://cafe.naver.com/f-e/cafes/15112066/menus/0?viewType=L&ta=ARTICLE_COMMENT&page=1&q=%ED%95%9C%ED%88%AC"
            page.goto(search_url2, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
            log(f"최종 URL: {page.url}")
            log(f"로그인 페이지로 리디렉션됨: {'nid.naver.com' in page.url}")
            rows2 = page.query_selector_all("a.article")
            log(f"검색 결과 게시글 링크 수: {len(rows2)}개")

            browser.close()
        send_result_email()
    except Exception as e:
        import traceback
        log(f"❌ 오류: {traceback.format_exc()}")
        send_result_email()
        raise

if __name__ == "__main__":
    main()
