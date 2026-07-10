"""
test_blog_search.py
- 네이버 블로그 검색 결과 페이지의 실제 HTML 구조 검증
- Playwright로 검색 → 결과 리스트 파싱 시도 → 결과 메일 발송
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

# 모바일 블로그 검색 - 최신순
SEARCH_URL = "https://m.search.naver.com/search.naver?where=m_blog&query=%ED%95%9C%EA%B5%AD%ED%88%AC%EC%9E%90%EC%A6%9D%EA%B6%8C&sm=mtb_opt&nso=so%3Add%2Cp%3Aall"

logs = []
def log(msg):
    line = f"[TEST] {msg}"
    print(line)
    logs.append(line)

def send_result_email():
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    body = "\n".join(logs)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[블로그 검색 검증] 결과 | {now_str} KST"
    msg["From"] = f"블로그검증봇 <{GMAIL_USER}>"
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(f"<pre>{body}</pre>", "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
    print("결과 메일 발송 완료")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
        )

        log(f"검색 URL 접근: {SEARCH_URL[:80]}")
        try:
            page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=20000)
            log(f"✅ 로드 성공 - 최종 URL: {page.url[:100]}")
        except Exception as e:
            log(f"❌ 로드 실패: {e}")
            browser.close()
            send_result_email()
            return

        page.wait_for_timeout(3000)

        # 후보 셀렉터들로 검색 결과 리스트 탐색
        candidate_selectors = [
            "li.bx",
            ".lst_type",
            "ul.list_news li",
            ".api_subject_bx li",
            "div.total_wrap",
            "a.title_link",
            ".news_area",
        ]

        for sel in candidate_selectors:
            items = page.query_selector_all(sel)
            log(f"셀렉터 '{sel}' → {len(items)}건 매칭")

        # 가장 유력한 후보로 실제 데이터 추출 시도
        log("\n--- 상세 추출 시도 (a 태그 자체 텍스트 + 부모구조) ---")
        anchors = page.query_selector_all("a[href*='blog.naver.com']")
        log(f"blog.naver.com 앵커 태그 총 {len(anchors)}개")

        seen_urls = set()
        post_count = 0
        for a in anchors:
            href = a.get_attribute("href") or ""
            # 게시글 URL만 (블로거 홈이 아닌 postId 포함된 것)
            import re
            if not re.search(r'/\d{6,}(\?|$)', href):
                continue
            if href in seen_urls:
                continue
            seen_urls.add(href)
            post_count += 1

            own_text = a.inner_text().strip()
            log(f"\n  [{post_count}] URL: {href}")
            log(f"       a태그 자체 텍스트: '{own_text[:80]}'")

            # 부모 요소들을 거슬러 올라가며 텍스트 확인
            try:
                parent = a.evaluate_handle("el => el.closest('li') || el.parentElement.parentElement")
                parent_text = parent.as_element().inner_text().strip() if parent.as_element() else ""
                log(f"       부모(li) 텍스트: '{parent_text[:200]}'")
            except Exception as e:
                log(f"       부모 텍스트 추출 오류: {e}")

            if post_count >= 8:
                break

        log(f"\n실제 게시글 URL(postId 포함) 고유 개수: {post_count}")

        # li.bx 첫 3개의 실제 innerHTML 구조 덤프 (정밀 셀렉터 확보용)
        log("\n--- li.bx innerHTML 구조 (첫 3개) ---")
        bx_items = page.query_selector_all("li.bx")
        for i, item in enumerate(bx_items[:3]):
            try:
                html = item.evaluate("el => el.outerHTML")
                # 너무 길면 자르기
                log(f"\n  [li.bx #{i}] HTML ({len(html)}자):")
                log(html[:1500])
            except Exception as e:
                log(f"  오류: {e}")

        # 전체 HTML 일부 덤프 (구조 파악용, 검색결과 영역만)
        log("\n--- HTML 구조 일부 ---")
        html = page.content()
        log(f"전체 HTML 길이: {len(html)}자")
        # api_subject_bx 또는 lst_total 영역 찾기
        import re
        m = re.search(r'<ul[^>]*class="[^"]*(?:lst_total|api_subject_bx)[^"]*"[^>]*>(.{0,800})', html, re.S)
        if m:
            log(f"결과 리스트 영역 HTML 일부:\n{m.group(1)[:800]}")
        else:
            log("⚠️ lst_total/api_subject_bx 패턴 미발견")

        log("\n=== 테스트 완료 ===")
        browser.close()
    send_result_email()

if __name__ == "__main__":
    main()
