"""
test_verify_post.py
- 07/11 08시 탐지된 게시글의 실제 본문+댓글을 확인
- "한투"가 본문/댓글 중 어디에 있는지(혹은 없는지) 검증
"""
import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_APP_PW = os.environ["GMAIL_APP_PW"]
COOKIE_JSON  = os.environ["NAVER_COOKIES_JSON"]

POST_URL = "https://cafe.naver.com/f-e/cafes/28497937/articles/1481428"

logs = []
def log(msg):
    print(msg)
    logs.append(msg)

def send_result_email():
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    body = "\n".join(logs)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[게시글 검증] 한투 언급 위치 확인 | {now_str} KST"
    msg["From"] = f"검증봇 <{GMAIL_USER}>"
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(f"<pre>{body}</pre>", "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())

def main():
    try:
        # monitor.py와 동일한 방식: 파일로 저장 후 storage_state=경로
        with open("naver_cookies.json", "w") as f:
            f.write(COOKIE_JSON)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state="naver_cookies.json")
            page = context.new_page()

            log(f"접근: {POST_URL}")
            page.goto(POST_URL, wait_until="domcontentloaded", timeout=20000)

            SELECTOR_COMBINED = ".se-main-container, .ArticleContentBox, .article-viewer, .ContentRenderer, #tbody, .article_body, .se-component-content"
            try:
                page.wait_for_selector(SELECTOR_COMBINED, timeout=8000)
            except Exception:
                page.wait_for_timeout(2500)

            all_frames = [(f.url or "") for f in page.frames]
            log(f"page.url: {page.url}")
            log(f"프레임 {len(all_frames)}개: {all_frames}")

            # 본문 찾기 (iframe 구조)
            body_text = ""
            for frame in page.frames:
                frame_url = frame.url or ""
                if "naver.com" in frame_url and frame_url != page.url and frame_url != "about:blank":
                    for sel in [".se-main-container", ".ArticleContentBox", "#tbody", ".article_body"]:
                        try:
                            el = frame.query_selector(sel)
                            if el:
                                text = el.inner_text().strip()
                                if text:
                                    body_text = text
                                    log(f"본문 프레임: {frame_url[:70]} (셀렉터={sel})")
                                    break
                        except Exception:
                            continue
                    if body_text:
                        break

            # fallback: 메인 페이지 직접
            if not body_text:
                for sel in [".se-main-container", ".ArticleContentBox", "#tbody", ".article_body"]:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            text = el.inner_text().strip()
                            if text:
                                body_text = text
                                log(f"본문 메인페이지 직접(셀렉터={sel})")
                                break
                    except Exception:
                        continue

            log(f"\n=== 본문 전체 ({len(body_text)}자) ===")
            log(body_text)

            keywords = ["한투", "한국투자증권", "뱅키스"]
            log(f"\n=== 본문 내 키워드 존재 여부 ===")
            for kw in keywords:
                log(f"  '{kw}' in 본문: {kw in body_text}")

            # 로그인 상태 확인 (실제 monitor.py와 동일하게)
            login_ok = False
            for frame in page.frames:
                try:
                    if frame.query_selector("a[href*='Logout']") or "profile" in (frame.url or ""):
                        login_ok = True
                except Exception:
                    pass
            log(f"로그인 상태(추정): {'확인됨' if login_ok else '불명 - 쿠키 만료 가능성 있음'}")

            # 댓글 찾기
            log(f"\n=== 댓글 수집 시도 ===")
            comment_text = ""
            comment_selectors = [
                ".comment_area", "[class*='CommentItem']", "[class*='comment_text']",
                ".comment_list", "ul.comment_list", "[class*='Comment']",
            ]
            for frame in page.frames:
                frame_url = frame.url or ""
                if "naver.com" in frame_url and frame_url != page.url and frame_url != "about:blank":
                    for sel in comment_selectors:
                        try:
                            els = frame.query_selector_all(sel)
                            for el in els:
                                t = el.inner_text().strip()
                                if t:
                                    comment_text += t + "\n---\n"
                        except Exception:
                            continue

            log(f"댓글 텍스트 길이: {len(comment_text)}자")
            if comment_text:
                log(f"\n=== 댓글 전체 ===")
                log(comment_text[:3000])
                log(f"\n=== 댓글 내 키워드 존재 여부 ===")
                for kw in keywords:
                    log(f"  '{kw}' in 댓글: {kw in comment_text}")
            else:
                log("⚠️ 댓글 텍스트 수집 실패 (셀렉터 미매칭 또는 댓글 없음)")

            browser.close()
        send_result_email()
    except Exception as e:
        import traceback
        log(f"❌ 오류: {traceback.format_exc()}")
        send_result_email()
        raise

if __name__ == "__main__":
    main()
